"""Golden numbers.

Each expected value is computed with SQL written straight from the data
dictionary, independently of the semantic layer and the compiler. If the two
disagree, the semantic layer has drifted from Meridian's definition.
"""
from __future__ import annotations

import pytest

from freshflow.checks import _stay_sentence, basis_sensitivity, caveats, verify_numbers
from freshflow.compiler import run
from freshflow.semantic import load
from freshflow.spec import QuerySpec, TimeRange
from freshflow.warehouse import connect

FRESH = "('Produce','Dairy','Meat','Bakery','Deli')"


@pytest.fixture(scope="module")
def con():
    return connect()


@pytest.fixture(scope="module")
def model():
    return load()


def reference(con, sql: str):
    return con.execute(sql).fetchone()


def test_shrink_cost_june_fresh(con, model):
    """Shrink cost for the fresh departments in June, both ways."""
    expected = reference(con, f"""
        WITH shipped AS (
          SELECT SUM(h.cases_received * i.case_size * i.unit_cost) v
          FROM shipments h JOIN items i USING(item_id)
          WHERE i.dept IN {FRESH} AND h.date BETWEEN '2026-06-01' AND '2026-06-30'),
        sold AS (
          SELECT SUM(s.units_sold * i.unit_cost) v
          FROM sales_daily s JOIN items i USING(item_id)
          WHERE i.dept IN {FRESH} AND s.date BETWEEN '2026-06-01' AND '2026-06-30')
        SELECT (SELECT v FROM shipped) - (SELECT v FROM sold)""")[0]

    spec = QuerySpec(measures=["shrink_cost"], time=TimeRange(start="2026-06-01", end="2026-06-30"))
    spec.validate_against(model)
    _, _, rows = run(con, spec, model)
    assert rows[0]["shrink_cost"] == pytest.approx(expected, rel=1e-9)


def test_shrink_units_june_fresh(con, model):
    expected = reference(con, f"""
        WITH shipped AS (
          SELECT SUM(h.cases_received * i.case_size) v
          FROM shipments h JOIN items i USING(item_id)
          WHERE i.dept IN {FRESH} AND h.date BETWEEN '2026-06-01' AND '2026-06-30'),
        sold AS (
          SELECT SUM(s.units_sold) v
          FROM sales_daily s JOIN items i USING(item_id)
          WHERE i.dept IN {FRESH} AND s.date BETWEEN '2026-06-01' AND '2026-06-30')
        SELECT (SELECT v FROM shipped) - (SELECT v FROM sold)""")[0]

    spec = QuerySpec(measures=["shrink_units"], time=TimeRange(start="2026-06-01", end="2026-06-30"))
    _, _, rows = run(con, spec, model)
    assert rows[0]["shrink_units"] == expected


def test_scope_changes_the_number(con, model):
    """Including Grocery is not cosmetic. It must move the total."""
    t = TimeRange(start="2026-06-01", end="2026-06-30")
    fresh = run(con, QuerySpec(measures=["shrink_cost"], time=t, scope="fresh"), model)[2][0]
    everything = run(con, QuerySpec(measures=["shrink_cost"], time=t, scope="all"), model)[2][0]
    assert everything["shrink_cost"] > fresh["shrink_cost"]
    assert (everything["shrink_cost"] - fresh["shrink_cost"]) / fresh["shrink_cost"] > 0.01


def test_basis_sensitivity_reports_both_changes(con, model):
    spec = QuerySpec(
        intent="comparison",
        measures=["shrink_cost"],
        time=TimeRange(start="2026-06-01", end="2026-06-30"),
        compare_time=TimeRange(start="2026-05-01", end="2026-05-31"),
    )
    s = basis_sensitivity(con, spec, model, [])
    assert s["primary"] == "shrink_cost" and s["other"] == "shrink_units"
    p, o = s["primary_change_pct"], s["other_change_pct"]
    assert s["disagrees"] == ((p >= 0) != (o >= 0) or abs(p - o) >= 5.0)
    if s["disagrees"]:
        assert "defensible" not in s["message"].lower()
        assert "mix shift" not in s["message"].lower()


def test_net_sales_matches_source(con, model):
    expected = reference(con, "SELECT SUM(net_sales) FROM sales_daily")[0]
    spec = QuerySpec(measures=["net_sales"], scope="all",
                     time=TimeRange(start="2026-04-01", end="2026-06-30"))
    assert run(con, spec, model)[2][0]["net_sales"] == pytest.approx(expected, rel=1e-9)


def test_zero_overlap_does_not_say_only_zero():
    assert _stay_sentence(0, 10) == "None of the 10 names stay."
    assert _stay_sentence(1, 10) == "Only 1 of the 10 names stays."
    assert _stay_sentence(3, 10) == "Only 3 of the 10 names stay."


def test_short_window_caveat_uses_plain_words(con, model):
    spec = QuerySpec(measures=["shrink_cost"], time=TimeRange(start="2026-06-01", end="2026-06-03"))
    text = " ".join(caveats(con, spec, model, [{"shrink_cost": 1}])).lower()
    assert "days" in text
    assert "lumps" not in text
    assert "grain" not in text
    assert "extract" not in text


def test_unknown_measure_is_rejected(model):
    spec = QuerySpec(measures=["gross_margin"], time=TimeRange(start="2026-06-01", end="2026-06-30"))
    with pytest.raises(KeyError):
        spec.validate_against(model)


def test_unknown_dimension_is_rejected(model):
    spec = QuerySpec(measures=["net_sales"], dimensions=["vendor"],
                     time=TimeRange(start="2026-06-01", end="2026-06-30"))
    with pytest.raises(KeyError):
        spec.validate_against(model)


def test_sql_injection_in_a_filter_is_quoted(con, model):
    spec = QuerySpec(measures=["net_sales"], time=TimeRange(start="2026-06-01", end="2026-06-30"),
                     filters=[{"field": "dept", "op": "in", "values": ["Dairy'; DROP TABLE items; --"]}])
    _, _, rows = run(con, spec, model)
    assert rows[0]["net_sales"] == 0
    assert con.execute("SELECT count(*) FROM items").fetchone()[0] == 201


def test_number_verification_catches_an_invented_figure():
    rows = [{"item": "Peaches", "shrink_cost": 2735.71}]
    assert verify_numbers("Peaches lost $2,736.", rows, []) == []
    assert verify_numbers("Peaches lost $9,412.", rows, []) == ["$9,412"]


def test_a_fall_stated_as_a_positive_percent_is_grounded():
    assert verify_numbers("Shrink fell 5.0% in June.", [], [-5.0]) == []


def test_derived_percentages_count_as_grounded():
    rows = [{"month": "2026-04", "units_sold": 11475}, {"month": "2026-06", "units_sold": 14175}]
    # 14175 / 11475 - 1 = 23.5%, which a reader can work out from the rows.
    assert verify_numbers("Units rose 23.5% from April to June.", rows, []) == []
    assert verify_numbers("Units rose 61.0% from April to June.", rows, []) == ["61.0%"]


def test_years_and_small_counts_are_not_treated_as_claims():
    rows = [{"dept": "Bakery", "shrink_rate_cost": 0.0945}]
    assert verify_numbers("In 2026 Bakery led the top 5 at 9.4%.", rows, []) == []


def test_planner_nulls_do_not_break_the_spec():
    """The planner emits null for fields it has no opinion on."""
    spec = QuerySpec.model_validate({
        "measures": ["net_sales"], "dimensions": None, "filters": None, "limit": None,
        "time": {"start": "2026-06-01", "end": "2026-06-30"},
    })
    assert spec.limit == 20 and spec.dimensions == [] and spec.filters == []


def test_filter_values_are_grounded_figures():
    rows = [{"shrink_cost": 8238.15}]
    assert verify_numbers("Store 104 shrank $8,238.", rows, [], labels=["104"]) == []
    assert verify_numbers("Store 109 shrank $8,238.", rows, [], labels=["104"]) == ["109"]
