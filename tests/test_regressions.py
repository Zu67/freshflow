"""Regressions for validation, check completeness, and answer-card errors."""
import json

import duckdb
import pytest
from pydantic import ValidationError

from freshflow import checks, llm
from freshflow.compiler import run
from freshflow.engine import Engine, _fact_kind
from freshflow.semantic import load
from freshflow.spec import Filter, QuerySpec, TimeRange


@pytest.fixture(scope="module")
def engine():
    instance = Engine()
    yield instance
    instance.con.close()


def plan(**updates):
    raw = {
        "answerable": True,
        "spec": {"measures": ["units_sold"],
                 "time": {"start": "2026-06-01", "end": "2026-06-30"}},
    }
    raw.update(updates)
    return raw


@pytest.mark.parametrize("op", ["in", "not_in", "eq", "contains"])
def test_empty_filter_is_rejected_before_sql(op):
    with pytest.raises(ValidationError, match="at least one"):
        Filter(field="dept", op=op, values=[])


def test_equality_filter_does_not_silently_drop_values():
    with pytest.raises(ValidationError, match="exactly one"):
        Filter(field="dept", op="eq", values=["Dairy", "Bakery"])


def test_negative_pair_count_is_not_a_ranked_sample():
    # 601 negative pairs in scope; same description on distinct item IDs.
    # Extra rows must be excluded by department, date, and explicit filter.
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE items AS SELECT n item_id, 'same name' description, "
                    "CASE WHEN n = 602 THEN 'Grocery' ELSE 'Dairy' END dept, 1 case_size "
                    "FROM range(604) t(n)")
        con.execute("CREATE TABLE fact AS SELECT item_id, "
                    "CASE WHEN item_id = 603 THEN 102 ELSE 101 END store_id, "
                    "CASE WHEN item_id = 601 THEN DATE '2026-05-01' "
                    "ELSE DATE '2026-06-01' END date, 0 cases_received, 1 units_sold FROM items")
        spec = QuerySpec(measures=["shrink_units"], limit=1,
                         filters=[Filter(field="store", values=["101"])],
                         time=TimeRange(start="2026-06-01", end="2026-06-30"))
        assert checks._negative_pairs(con, spec, load()) == 601
    finally:
        con.close()


def test_two_unparseable_model_replies_return_error_card(engine, monkeypatch):
    replies = iter(["not JSON", "still not JSON"])
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: next(replies))
    card = engine.ask("June sales")
    assert not card.answerable
    assert card.error
    assert "JSON" in card.answer
    assert card.rows == []


@pytest.mark.parametrize("raw", [
    plan(notes=None), plan(notes="not a list"), plan(restatement=[]),
    {"answerable": False, "reason": ["invalid"]},
    {"answerable": True},
    plan(spec={"measures": ["unknown"], "time": {"start": "2026-06-01", "end": "2026-06-30"}}),
    plan(spec={"measures": ["net_sales"], "filters": [{"field": "dept", "values": []}],
               "time": {"start": "2026-06-01", "end": "2026-06-30"}}),
])
def test_invalid_plan_returns_error_card(engine, monkeypatch, raw):
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: json.dumps(raw))
    card = engine.ask("June sales")
    assert not card.answerable
    assert card.error
    assert card.answer
    assert card.rows == []


def test_valid_refusal_is_not_a_system_error(engine, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: json.dumps(
        {"answerable": False, "reason": "No supplier column."}))
    card = engine.ask("Sales by supplier")
    assert not card.answerable
    assert not card.error
    assert card.answer == "No supplier column."


def test_store_count_is_answered_without_a_model(engine, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("the model should not be asked how many stores")

    monkeypatch.setattr(llm, "complete_json", boom)
    card = engine.ask("how many stores are there")
    assert card.answerable
    assert card.answer == "There are 10 stores in this extract."
    assert card.unverified_figures == []


def test_a_store_question_with_more_in_it_still_goes_to_the_planner(engine, monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda *_a, **_k: {"answerable": False, "reason": "No."})
    card = engine.ask("how many stores had shrink last month")
    assert not card.answerable
    assert card.answer == "No."


def test_store_count_phrasing_is_answered_without_a_model(engine, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("the model should not be asked how many stores")

    monkeypatch.setattr(llm, "complete_json", boom)
    card = engine.ask("how many stores do we operate")
    assert card.answer == "There are 10 stores in this extract."
    items = engine.ask("how many items do we carry")
    assert items.answer == "There are 201 items in this extract."
    window = engine.ask("what is the date range")
    assert "April 1" in window.answer and "June 30, 2026" in window.answer
    assert _fact_kind("What is 5 times 5") is None
    assert _fact_kind("how many stores had shrink last month") is None


def test_apples_matches_the_fruit_and_not_juice_or_pie(engine):
    spec = QuerySpec(
        measures=["units_sold"],
        dimensions=["item"],
        filters=[Filter(field="item", op="contains", values=["apples"])],
        scope="all",
        time=TimeRange(start="2026-04-01", end="2026-06-30"),
        limit=20,
    )
    names = [row["item"] for row in run(engine.con, spec, engine.model)[2]]
    assert "Honeycrisp Apples" in names
    assert "Gala Apples" in names
    assert all("Juice" not in name and "Pie" not in name for name in names)


def test_a_shortened_item_word_uses_the_word_in_the_question(engine, monkeypatch):
    raw = plan()
    raw["spec"].update({
        "dimensions": ["item"],
        "filters": [{"field": "item", "op": "contains", "values": ["apple"]}],
        "scope": "all",
        "limit": 20,
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: json.dumps(raw))
    card = engine.ask("How many apples were sold in June")
    names = [row["item"] for row in card.rows]
    assert card.spec["filters"][0]["values"] == ["apples"]
    assert "Honeycrisp Apples" in names
    assert all("Juice" not in name and "Pie" not in name for name in names)


def test_why_says_the_numbers_do_not_show_a_cause(engine, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: json.dumps(plan()))
    card = engine.ask("why were units sold higher in June")
    assert card.answerable
    assert "The numbers show what moved, but they don't say what caused it." in card.answer
    assert card.headline["parts"][-1]["text"].endswith("The numbers show what moved, but they don't say what caused it.")


def _shrink_plan(**spec_updates):
    raw = plan()
    raw["spec"].update({
        "measures": ["shrink_cost"],
        "time": {"start": "2026-06-01", "end": "2026-06-30", "label": "June"},
    })
    raw["spec"].update(spec_updates)
    return raw


def test_the_basis_toggle_swaps_shrink_dollars_for_units(engine, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: json.dumps(_shrink_plan()))
    card = engine.ask("June shrink", basis="units")
    assert card.spec["measures"] == ["shrink_units"]
    assert "shrink_units" in card.rows[0]
    assert "shrink_cost" not in card.rows[0]
    assert "units" in card.answer
    assert card.unverified_figures == []


def test_the_basis_toggle_can_show_both(engine, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: json.dumps(_shrink_plan()))
    card = engine.ask("June shrink", basis="both")
    assert card.spec["measures"] == ["shrink_cost", "shrink_units"]
    assert "$" in card.answer and "units" in card.answer
    assert card.unverified_figures == []
    assert not (card.headline or {}).get("note")


def test_the_basis_toggle_repoints_a_ranking(engine, monkeypatch):
    raw = _shrink_plan(dimensions=["dept"], order_by="shrink_cost")
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: json.dumps(raw))
    card = engine.ask("Shrink by department in June", basis="units")
    assert card.spec["measures"] == ["shrink_units"]
    assert card.spec["order_by"] == "shrink_units"


def test_the_basis_toggle_leaves_sales_alone(engine, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: json.dumps(plan()))
    card = engine.ask("June units sold", basis="dollars")
    assert card.spec["measures"] == ["units_sold"]


def test_answer_is_composed_from_the_rows(engine, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: json.dumps(plan()))
    card = engine.ask("June units sold")
    assert card.answerable
    assert card.rows[0]["units_sold"] > 0
    assert card.sql
    assert " units were sold" in card.answer
    assert card.headline["parts"]
    assert [s["label"] for s in card.headline["sources"][:2]] == ["Counted", "Formula"]
    assert "2026-06-01" in card.headline["sources"][0]["text"]
    assert "Grocery is left out" in card.headline["sources"][0]["text"]
    assert card.unverified_figures == []


def test_answer_card_carries_semantic_units(engine, monkeypatch):
    replies = iter([json.dumps(plan()), "See the rows."])
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: next(replies))
    card = engine.ask("June units sold").to_dict()
    assert card["column_units"] == {"units_sold": "units"}
    assert card["scope_note"]["unit"] == "units"
