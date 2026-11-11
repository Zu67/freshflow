"""The answer sentence is assembled from the rows, in one fixed shape."""
from datetime import date

from freshflow.narrator import compose
from freshflow.semantic import load
from freshflow.spec import QuerySpec, TimeRange


def _time(start, end, label=""):
    return TimeRange(start=date.fromisoformat(start), end=date.fromisoformat(end), label=label)


def test_one_total_names_the_number_and_the_prior_number():
    model = load()
    spec = QuerySpec(
        measures=["shrink_cost"],
        time=_time("2026-06-01", "2026-06-30", "June"),
        compare_time=_time("2026-05-01", "2026-05-31", "May"),
    )
    text, headline = compose(model, spec, [{"shrink_cost": 1200.4}], [{"shrink_cost": 900}])
    assert text == "In June, shrink cost $1,200.4, up from $900 in May."
    assert headline["parts"][-1] == {"text": "up from $900 in May.", "tone": "up"}
    assert headline["table"] is None
    assert "%" not in text


def test_a_drop_in_shrink_is_called_down():
    model = load()
    spec = QuerySpec(
        measures=["shrink_units"],
        time=_time("2026-06-01", "2026-06-30"),
        compare_time=_time("2026-05-01", "2026-05-31"),
    )
    text, headline = compose(model, spec, [{"shrink_units": 10}], [{"shrink_units": 40}])
    assert text == "Shrink came to 10 units, down from 40 in the period before."
    assert headline["parts"][-1]["tone"] == "down"


def test_sales_increase_is_not_marked_as_shrink():
    model = load()
    spec = QuerySpec(
        measures=["net_sales"],
        scope="all",
        time=_time("2026-06-01", "2026-06-30"),
        compare_time=_time("2026-05-01", "2026-05-31"),
    )
    text, headline = compose(model, spec, [{"net_sales": 50}], [{"net_sales": 40}])
    assert text == "Net sales came to $50, up from $40 in the period before."
    assert headline["parts"][-1]["tone"] == ""


def test_units_sold_leads_with_the_number():
    model = load()
    spec = QuerySpec(
        measures=["units_sold"],
        time=_time("2026-06-01", "2026-06-30", "June"),
        compare_time=_time("2026-05-01", "2026-05-31", "May"),
    )
    text, _ = compose(model, spec, [{"units_sold": 250}], [{"units_sold": 200}])
    assert text == "In June, 250 units were sold, up from 200 in May."


def test_an_unchanged_total_says_so_plainly():
    model = load()
    spec = QuerySpec(
        measures=["shrink_cost"],
        time=_time("2026-06-01", "2026-06-30", "June"),
        compare_time=_time("2026-05-01", "2026-05-31", "May"),
    )
    text, _ = compose(model, spec, [{"shrink_cost": 900}], [{"shrink_cost": 900}])
    assert text == "In June, shrink cost $900, the same as in May."


def test_a_breakdown_is_one_answer_line_per_row():
    model = load()
    spec = QuerySpec(
        measures=["shrink_units"],
        dimensions=["dept"],
        time=_time("2026-06-01", "2026-06-30", "June"),
        compare_time=_time("2026-05-01", "2026-05-31", "May"),
    )
    rows = [
        {"dept": "Produce", "shrink_units": 10},
        {"dept": "Dairy", "shrink_units": 4},
    ]
    prior = [
        {"dept": "Dairy", "shrink_units": 9},
        {"dept": "Produce", "shrink_units": 8},
    ]
    text, headline = compose(model, spec, rows, prior)
    assert text == (
        "In June, Produce had the most shrink — 10 units, up from 8 in May. "
        "Dairy was next — 4 units, down from 9."
    )
    assert [part["tone"] for part in headline["parts"] if part["tone"]] == ["up", "down"]
    assert headline["table"]["headers"][:3] == ["Department", "June", "May"]
    assert headline["table"]["rows"][0][0]["text"] == "Produce"
    assert headline["table"]["rows"][1][3]["value"] == -5


def test_nothing_matched_says_so():
    model = load()
    spec = QuerySpec(measures=["units_sold"], time=_time("2026-06-01", "2026-06-30"))
    text, headline = compose(model, spec, [], [])
    assert text == "Nothing in the extract matches that — try a different item or department name."
    assert headline["table"] is None


def test_a_store_group_is_capitalised_in_the_sentence():
    model = load()
    spec = QuerySpec(
        measures=["shrink_units"],
        dimensions=["store"],
        time=_time("2026-06-01", "2026-06-30", "June"),
    )
    text, headline = compose(model, spec, [{"store": "101", "shrink_units": 12}], [])
    assert text == "In June, Store 101 had the most shrink — 12 units."
    assert headline["table"] is None
