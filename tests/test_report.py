"""Weekly report: dates, population, and numbers checked against the source tables."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from freshflow.app import report, report_weeks
from freshflow.report import ReportError, build_report
from freshflow.semantic import load
from freshflow.warehouse import connect

FULL = date(2026, 6, 22)
PRIOR = date(2026, 6, 15)
FIRST = date(2026, 3, 30)
LAST = date(2026, 6, 29)


@pytest.fixture(scope="module")
def con():
    connection = connect()
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def model():
    return load()


def _dept_list(names: list[str]) -> str:
    return ", ".join("'" + name + "'" for name in names)


def source_by_dept(con, measure: str, start: str, end: str, departments: list[str], store_id: int | None = None):
    """Shrink by department from shipments and sales, not from the report SQL."""
    depts = _dept_list(departments)
    store_h = f"AND h.store_id = {store_id}" if store_id else ""
    store_s = f"AND s.store_id = {store_id}" if store_id else ""
    if measure == "shrink_cost":
        shipped, sold = "h.cases_received * i.case_size * i.unit_cost", "s.units_sold * i.unit_cost"
    else:
        shipped, sold = "h.cases_received * i.case_size", "s.units_sold"
    rows = con.execute(f"""
        WITH shipped AS (
          SELECT i.dept, SUM({shipped}) v
          FROM shipments h JOIN items i USING (item_id)
          WHERE i.dept IN ({depts}) AND h.date BETWEEN DATE '{start}' AND DATE '{end}' {store_h}
          GROUP BY i.dept
        ),
        sold AS (
          SELECT i.dept, SUM({sold}) v
          FROM sales_daily s JOIN items i USING (item_id)
          WHERE i.dept IN ({depts}) AND s.date BETWEEN DATE '{start}' AND DATE '{end}' {store_s}
          GROUP BY i.dept
        )
        SELECT d.dept, COALESCE(h.v, 0) - COALESCE(s.v, 0) AS value
        FROM (SELECT UNNEST([{depts}]) AS dept) d
        LEFT JOIN shipped h ON h.dept = d.dept
        LEFT JOIN sold s ON s.dept = d.dept
    """).fetchall()
    return {dept: float(value) for dept, value in rows}


def source_top_items(con, measure, departments, start, end, prior_start, prior_end, store_id=None):
    depts = _dept_list(departments)
    store_h = f"AND h.store_id = {store_id}" if store_id else ""
    store_s = f"AND s.store_id = {store_id}" if store_id else ""
    if measure == "shrink_cost":
        shipped, sold = "h.cases_received * i.case_size * i.unit_cost", "s.units_sold * i.unit_cost"
    else:
        shipped, sold = "h.cases_received * i.case_size", "s.units_sold"
    scan_start, scan_end = min(start, prior_start), max(end, prior_end)
    rows = con.execute(f"""
        WITH shipped AS (
          SELECT h.item_id,
                 SUM(CASE WHEN h.date BETWEEN DATE '{start}' AND DATE '{end}' THEN {shipped} END) cur_ship,
                 SUM(CASE WHEN h.date BETWEEN DATE '{prior_start}' AND DATE '{prior_end}' THEN {shipped} END) prev_ship
          FROM shipments h JOIN items i USING (item_id)
          WHERE i.dept IN ({depts}) {store_h}
            AND h.date BETWEEN DATE '{scan_start}' AND DATE '{scan_end}'
          GROUP BY h.item_id
        ),
        sold AS (
          SELECT s.item_id,
                 SUM(CASE WHEN s.date BETWEEN DATE '{start}' AND DATE '{end}' THEN {sold} END) cur_sold,
                 SUM(CASE WHEN s.date BETWEEN DATE '{prior_start}' AND DATE '{prior_end}' THEN {sold} END) prev_sold
          FROM sales_daily s JOIN items i USING (item_id)
          WHERE i.dept IN ({depts}) {store_s}
            AND s.date BETWEEN DATE '{scan_start}' AND DATE '{scan_end}'
          GROUP BY s.item_id
        )
        SELECT i.item_id,
               COALESCE(sh.cur_ship, 0) - COALESCE(so.cur_sold, 0),
               COALESCE(sh.prev_ship, 0) - COALESCE(so.prev_sold, 0)
        FROM items i
        LEFT JOIN shipped sh ON sh.item_id = i.item_id
        LEFT JOIN sold so ON so.item_id = i.item_id
        WHERE i.dept IN ({depts})
    """).fetchall()
    ranked = []
    for item_id, current, prior in rows:
        change = float(current or 0) - float(prior or 0)
        if change > 0:
            ranked.append((change, int(item_id), float(current or 0), float(prior or 0)))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return ranked[:10]


def test_weeks_cover_the_extract_monday_to_sunday():
    weeks = report_weeks()["weeks"]
    assert len(weeks) == 14
    assert all(date.fromisoformat(week["week_start"]).weekday() == 0 for week in weeks)
    assert weeks[0]["week_start"] == "2026-03-30"
    assert weeks[0]["counted_start"] == "2026-04-01"
    assert weeks[0]["counted_end"] == "2026-04-05"
    assert weeks[0]["partial"] is True
    full = next(week for week in weeks if week["week_start"] == "2026-06-22")
    assert full["counted_start"] == "2026-06-22" and full["counted_end"] == "2026-06-28"
    assert full["days"] == 7 and full["partial"] is False
    assert weeks[-1]["week_start"] == "2026-06-29"
    assert weeks[-1]["counted_end"] == "2026-06-30"
    assert weeks[-1]["days"] == 2
    assert "partial week" in weeks[0]["menu_label"]
    assert "no prior week" in weeks[0]["menu_label"]
    assert "partial week" in weeks[-1]["menu_label"]
    assert "no prior week" not in full["menu_label"]


def test_finance_week_matches_source_and_names_every_item(con, model):
    built = build_report(con, model, "finance", FULL)
    assert built["measure"] == "shrink_cost"
    assert built["unit"] == "dollars"
    assert built["departments"] == ["Produce", "Dairy", "Meat", "Bakery", "Deli", "Grocery"]
    assert built["prior_week"]["counted_start"] == "2026-06-15"
    assert built["prior_week"]["counted_end"] == "2026-06-21"
    assert "Grocery" in built["counted"][4]
    assert "item_id" in " ".join(built["counted"])

    expected = source_by_dept(con, "shrink_cost", "2026-06-22", "2026-06-28", built["departments"])
    expected_prior = source_by_dept(con, "shrink_cost", "2026-06-15", "2026-06-21", built["departments"])
    assert [row["dept"] for row in built["departments_table"]] == built["departments"]
    for row in built["departments_table"]:
        assert row["current"] == pytest.approx(expected[row["dept"]], abs=0.01)
        assert row["prior"] == pytest.approx(expected_prior[row["dept"]], abs=0.01)
        assert row["change"] == pytest.approx(row["current"] - row["prior"], abs=0.01)
    assert built["department_total"]["current"] == pytest.approx(
        sum(row["current"] for row in built["departments_table"]), abs=0.01
    )
    fresh_only = source_by_dept(
        con, "shrink_cost", "2026-06-22", "2026-06-28",
        [name for name in built["departments"] if name != "Grocery"],
    )
    assert built["department_total"]["current"] != pytest.approx(sum(fresh_only.values()), abs=0.01)

    by_id = {item["item_id"]: item for item in built["included_items"]}
    assert len(by_id) == 201
    assert by_id[10008]["description"] == "Honeycrisp Apples"
    assert by_id[10009]["description"] == "Gala Apples"
    assert by_id[10071] == {"item_id": 10071, "description": "Apple Juice 64oz", "dept": "Dairy"}
    assert by_id[10130]["description"] == "Apple Pie 9in"
    assert by_id[10071]["item_id"] != by_id[10008]["item_id"]

    ranked = source_top_items(
        con, "shrink_cost", built["departments"], "2026-06-22", "2026-06-28", "2026-06-15", "2026-06-21"
    )
    assert [row["item_id"] for row in built["top_items"]] == [row[1] for row in ranked]
    assert len(built["top_items"]) <= 10
    for shown, raw in zip(built["top_items"], ranked):
        assert shown["change"] > 0
        assert shown["current"] == pytest.approx(raw[2], abs=0.01)
        assert shown["prior"] == pytest.approx(raw[3], abs=0.01)
        assert shown["description"]
    changes = [row["change"] for row in built["top_items"]]
    assert changes == sorted(changes, reverse=True)


def test_ops_is_units_by_store_and_excludes_grocery(con, model):
    built = build_report(con, model, "ops", FULL)
    assert built["measure"] == "shrink_units"
    assert built["unit"] == "units"
    assert "Grocery" not in built["departments"]
    assert built["departments"] == ["Produce", "Dairy", "Meat", "Bakery", "Deli"]
    assert len(built["included_items"]) == 161
    assert {item["dept"] for item in built["included_items"]} == set(built["departments"])
    assert [store["store_id"] for store in built["stores"]] == list(range(101, 111))

    store = built["stores"][0]
    assert store["store_id"] == 101
    assert store["banner"] == "Meridian Foods" and store["region"] == "Northeast"
    expected = source_by_dept(con, "shrink_units", "2026-06-22", "2026-06-28", built["departments"], store_id=101)
    assert [row["dept"] for row in store["departments"]] == built["departments"]
    for row in store["departments"]:
        assert row["current"] == expected[row["dept"]]
        assert isinstance(row["current"], int)
    assert store["department_total"]["current"] == sum(row["current"] for row in store["departments"])

    ranked = source_top_items(
        con, "shrink_units", built["departments"], "2026-06-22", "2026-06-28", "2026-06-15", "2026-06-21",
        store_id=101,
    )
    assert [row["item_id"] for row in store["top_items"]] == [row[1] for row in ranked]
    assert all(row["dept"] != "Grocery" for row in store["top_items"])
    assert "Grocery" not in built["sql"]["departments"]


def test_finance_can_be_viewed_in_units(con, model):
    built = build_report(con, model, "finance", FULL, "units")
    assert built["basis"] == "units"
    assert built["measure"] == "shrink_units"
    assert built["unit"] == "units"
    assert built["departments"] == ["Produce", "Dairy", "Meat", "Bakery", "Deli", "Grocery"]
    expected = source_by_dept(con, "shrink_units", "2026-06-22", "2026-06-28", built["departments"])
    for row in built["departments_table"]:
        assert row["current"] == expected[row["dept"]]
        assert "also" not in row
    ranked = source_top_items(
        con, "shrink_units", built["departments"], "2026-06-22", "2026-06-28", "2026-06-15", "2026-06-21"
    )
    assert [row["item_id"] for row in built["top_items"]] == [row[1] for row in ranked]


def test_both_keeps_the_team_measure_first_and_ranks_by_it(con, model):
    built = build_report(con, model, "finance", FULL, "both")
    assert built["basis"] == "both"
    assert built["measures"] == ["shrink_cost", "shrink_units"]
    assert built["ranked_by"] == "shrink_cost"
    assert "ranked by" in " ".join(built["counted"])
    expected_units = source_by_dept(con, "shrink_units", "2026-06-22", "2026-06-28", built["departments"])
    for row in built["departments_table"]:
        assert row["also"]["measure"] == "shrink_units"
        assert row["also"]["current"] == expected_units[row["dept"]]
    assert built["department_total"]["also"]["current"] == sum(expected_units.values())
    ranked = source_top_items(
        con, "shrink_cost", built["departments"], "2026-06-22", "2026-06-28", "2026-06-15", "2026-06-21"
    )
    assert [row["item_id"] for row in built["top_items"]] == [row[1] for row in ranked]
    assert built["top_items"][0]["also"]["unit"] == "units"

    ops = build_report(con, model, "ops", FULL, "both")
    assert ops["measures"] == ["shrink_units", "shrink_cost"]
    assert ops["ranked_by"] == "shrink_units"
    store = ops["stores"][0]
    assert store["department_total"]["also"]["measure"] == "shrink_cost"
    assert store["top_items"][0]["item_id"] == 10041


def test_a_bad_basis_is_rejected(con, model):
    with pytest.raises(ReportError, match="dollars, units, or both"):
        build_report(con, model, "finance", FULL, "bananas")


def test_missing_prior_week_is_blank_not_zero(con, model):
    built = build_report(con, model, "finance", FIRST)
    assert built["comparable"] is False
    assert built["prior_week"]["available"] is False
    assert built["top_items"] == []
    assert built["sql"]["top_items"] is None
    assert "not shown as zero" in " ".join(built["counted"])
    assert any("no prior-week column" in note for note in built["notes"])
    for row in built["departments_table"]:
        assert row["prior"] is None and row["change"] is None
    expected = source_by_dept(con, "shrink_cost", "2026-04-01", "2026-04-05", built["departments"])
    for row in built["departments_table"]:
        assert row["current"] == pytest.approx(expected[row["dept"]], abs=0.01)
    ops = build_report(con, model, "ops", FIRST)
    assert ops["stores"] and all(store["top_items"] == [] for store in ops["stores"])
    assert all(row["prior"] is None for store in ops["stores"] for row in store["departments"])


def test_uneven_edge_week_says_the_day_counts(con, model):
    built = build_report(con, model, "ops", LAST)
    assert built["week"]["days"] == 2
    assert built["prior_week"]["days"] == 7
    assert any("2 days" in note and "7 days" in note for note in built["notes"])
    store = next(row for row in built["stores"] if row["store_id"] == 105)
    expected = source_by_dept(con, "shrink_units", "2026-06-29", "2026-06-30", built["departments"], store_id=105)
    for row in store["departments"]:
        assert row["current"] == expected[row["dept"]]


def test_bad_team_or_week_is_rejected(con, model):
    with pytest.raises(ReportError, match="finance or ops"):
        build_report(con, model, "merchandising", FULL)
    with pytest.raises(ReportError, match="Monday"):
        build_report(con, model, "finance", date(2026, 6, 23))
    with pytest.raises(HTTPException) as exc:
        report("finance", "2026-06-23")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        report("finance", "next week")
