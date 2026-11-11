"""Weekly shrink reports for finance and store operations.

The report does not ask a model to pick a measure, a department, or a date.
The team and the Monday set those, and the SQL uses the catalog formulas.
"""
from __future__ import annotations

from datetime import date, timedelta

from .semantic import SemanticModel

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class ReportError(ValueError):
    """The team or week cannot be reported from this extract."""


def _q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _md(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.day}"


def _span_label(start: date, end: date, partial: bool, week_start: date) -> str:
    if start.year == end.year and start.month == end.month:
        label = f"{_md(start)}–{end.day}, {start.year}"
    elif start.year == end.year:
        label = f"{_md(start)}–{_md(end)}, {start.year}"
    else:
        label = f"{_md(start)}, {start.year}–{_md(end)}, {end.year}"
    if partial:
        return f"{label} · partial week of {_md(week_start)}"
    return label


def _clip(start: date, end: date, window_start: date, window_end: date) -> tuple[date, date] | None:
    counted_start = max(start, window_start)
    counted_end = min(end, window_end)
    if counted_start > counted_end:
        return None
    return counted_start, counted_end


class _Week:
    def __init__(self, start: date, end: date, counted: tuple[date, date] | None):
        self.start = start
        self.end = end
        self.counted_start = counted[0] if counted else None
        self.counted_end = counted[1] if counted else None

    @property
    def available(self) -> bool:
        return self.counted_start is not None

    @property
    def days(self) -> int:
        if not self.available:
            return 0
        return (self.counted_end - self.counted_start).days + 1

    @property
    def partial(self) -> bool:
        return self.available and (self.counted_start != self.start or self.counted_end != self.end)

    def label(self) -> str:
        if not self.available:
            return f"{_md(self.start)}–{_md(self.end)}, {self.start.year} · outside the extract"
        return _span_label(self.counted_start, self.counted_end, self.partial, self.start)

    def as_dict(self) -> dict:
        return {
            "week_start": self.start.isoformat(),
            "week_end": self.end.isoformat(),
            "counted_start": self.counted_start.isoformat() if self.available else None,
            "counted_end": self.counted_end.isoformat() if self.available else None,
            "days": self.days,
            "partial": self.partial,
            "available": self.available,
            "label": self.label(),
        }


def _weeks(window_start: date, window_end: date) -> list[_Week]:
    monday = window_start - timedelta(days=window_start.weekday())
    last = window_end - timedelta(days=window_end.weekday())
    found = []
    while monday <= last:
        end = monday + timedelta(days=6)
        counted = _clip(monday, end, window_start, window_end)
        if counted:
            found.append(_Week(monday, end, counted))
        monday += timedelta(days=7)
    return found


def _prior(week: _Week, window_start: date, window_end: date) -> _Week:
    start = week.start - timedelta(days=7)
    end = week.end - timedelta(days=7)
    return _Week(start, end, _clip(start, end, window_start, window_end))


def list_weeks(model: SemanticModel) -> list[dict]:
    """Monday–Sunday weeks that overlap the extract, with the dates actually inside it."""
    rows = []
    for week in _weeks(model.window_start, model.window_end):
        row = week.as_dict()
        prior = _prior(week, model.window_start, model.window_end)
        menu = row["label"]
        if not prior.available:
            menu += " · no prior week"
        elif prior.partial:
            menu += " · prior week is partial"
        row["menu_label"] = menu
        rows.append(row)
    return rows


def _require_week(model: SemanticModel, week_start: date) -> _Week:
    for week in _weeks(model.window_start, model.window_end):
        if week.start == week_start:
            return week
    raise ReportError(
        f"{week_start.isoformat()} is not the Monday of a week in the extract "
        f"({model.window_start.isoformat()} through {model.window_end.isoformat()}). "
        "Weeks run Monday through Sunday."
    )


def _team_spec(model: SemanticModel, team: str) -> dict:
    if team == "finance":
        return {
            "team": "finance",
            "team_label": "Finance",
            "measure": "shrink_cost",
            "by_store": False,
            "departments": model.fresh_departments + model.center_store_departments,
        }
    if team == "ops":
        return {
            "team": "ops",
            "team_label": "Store operations",
            "measure": "shrink_units",
            "by_store": True,
            "departments": list(model.fresh_departments),
        }
    raise ReportError("Choose finance or ops. The report does not pick a team.")


def _digits(measure: str) -> int:
    return 2 if measure == "shrink_cost" else 0


def _num(value, digits: int):
    number = round(float(value or 0), digits)
    return int(number) if digits == 0 else number


def _values(current, prior, comparable: bool, digits: int) -> dict:
    current_n = _num(current, digits)
    if not comparable:
        return {"current": current_n, "prior": None, "change": None}
    prior_n = _num(prior, digits)
    return {"current": current_n, "prior": prior_n, "change": _num(current_n - prior_n, digits)}


def _measure_expr(measure: str, start: date, end: date) -> str:
    when = f"f.date BETWEEN DATE {_q(start.isoformat())} AND DATE {_q(end.isoformat())}"
    if measure == "shrink_cost":
        shipped, sold = "f.cases_received * i.case_size * i.unit_cost", "f.units_sold * i.unit_cost"
    elif measure == "shrink_units":
        shipped, sold = "f.cases_received * i.case_size", "f.units_sold"
    else:
        raise ReportError(f"unsupported measure {measure}")
    return (
        f"(COALESCE(SUM(CASE WHEN {when} THEN {shipped} END), 0)"
        f" - COALESCE(SUM(CASE WHEN {when} THEN {sold} END), 0))"
    )


def _dept_sql(spec: dict, week: _Week, prior: _Week) -> str:
    measures = spec["measures"]
    depts = ", ".join(_q(d) for d in spec["departments"])
    values = ", ".join(f"({_q(d)}, {i})" for i, d in enumerate(spec["departments"]))
    scan_end = week.counted_end
    current_cols = ",\n         ".join(
        f"{_measure_expr(m, week.counted_start, week.counted_end)} AS {m}_current" for m in measures
    )
    select_prior = ""
    if prior.available:
        select_prior = ",\n         " + ",\n         ".join(
            f"{_measure_expr(m, prior.counted_start, prior.counted_end)} AS {m}_prior" for m in measures
        )
        scan_end = max(week.counted_end, prior.counted_end)
    outer_cols = ",\n       ".join(
        f"COALESCE(a.{m}_current, 0) AS {m}_current,\n       "
        + (f"COALESCE(a.{m}_prior, 0) AS {m}_prior" if prior.available else f"NULL AS {m}_prior")
        for m in measures
    )
    scan_start = week.counted_start if not prior.available else min(week.counted_start, prior.counted_start)
    date_filter = (
        f"f.date BETWEEN DATE {_q(scan_start.isoformat())} AND DATE {_q(scan_end.isoformat())}"
    )
    if spec["by_store"]:
        return f"""WITH stores AS (
  SELECT store_id, MIN(banner) AS banner, MIN(region) AS region
  FROM fact
  GROUP BY store_id
),
agg AS (
  SELECT f.store_id, i.dept,
         {current_cols}{select_prior}
  FROM fact f
  JOIN items i ON i.item_id = f.item_id
  WHERE i.dept IN ({depts})
    AND {date_filter}
  GROUP BY f.store_id, i.dept
)
SELECT s.store_id, s.banner, s.region, d.dept,
       {outer_cols}
FROM stores s
CROSS JOIN (VALUES {values}) d(dept, ord)
LEFT JOIN agg a ON a.store_id = s.store_id AND a.dept = d.dept
ORDER BY s.store_id, d.ord"""
    return f"""WITH agg AS (
  SELECT i.dept,
         {current_cols}{select_prior}
  FROM fact f
  JOIN items i ON i.item_id = f.item_id
  WHERE i.dept IN ({depts})
    AND {date_filter}
  GROUP BY i.dept
)
SELECT d.dept,
       {outer_cols}
FROM (VALUES {values}) d(dept, ord)
LEFT JOIN agg a ON a.dept = d.dept
ORDER BY d.ord"""


def _top_sql(spec: dict, week: _Week, prior: _Week) -> str:
    primary = spec["measure"]
    measures = spec["measures"]
    depts = ", ".join(_q(d) for d in spec["departments"])
    agg_cols = ",\n         ".join(
        f"{_measure_expr(m, week.counted_start, week.counted_end)} AS {m}_current,\n"
        f"         {_measure_expr(m, prior.counted_start, prior.counted_end)} AS {m}_prior"
        for m in measures
    )
    value_cols = ", ".join(
        f"{m}_current, {m}_prior" for m in measures
    )
    scan_start = min(week.counted_start, prior.counted_start)
    scan_end = max(week.counted_end, prior.counted_end)
    date_filter = (
        f"f.date BETWEEN DATE {_q(scan_start.isoformat())} AND DATE {_q(scan_end.isoformat())}"
    )
    if spec["by_store"]:
        return f"""WITH agg AS (
  SELECT f.store_id, i.item_id,
         MIN(i.description) AS description,
         MIN(i.dept) AS dept,
         {agg_cols}
  FROM fact f
  JOIN items i ON i.item_id = f.item_id
  WHERE i.dept IN ({depts})
    AND {date_filter}
  GROUP BY f.store_id, i.item_id
)
SELECT store_id, item_id, description, dept, {value_cols}, change
FROM (
  SELECT store_id, item_id, description, dept, {value_cols},
         {primary}_current - {primary}_prior AS change
  FROM agg
) ranked
WHERE change > 0
QUALIFY ROW_NUMBER() OVER (PARTITION BY store_id ORDER BY change DESC, item_id) <= 10
ORDER BY store_id, change DESC, item_id"""
    return f"""WITH agg AS (
  SELECT i.item_id,
         MIN(i.description) AS description,
         MIN(i.dept) AS dept,
         {agg_cols}
  FROM fact f
  JOIN items i ON i.item_id = f.item_id
  WHERE i.dept IN ({depts})
    AND {date_filter}
  GROUP BY i.item_id
)
SELECT item_id, description, dept, {value_cols}, change
FROM (
  SELECT item_id, description, dept, {value_cols},
         {primary}_current - {primary}_prior AS change
  FROM agg
) ranked
WHERE change > 0
ORDER BY change DESC, item_id
LIMIT 10"""


def _query(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _day_word(days: int) -> str:
    return "day" if days == 1 else "days"


def _counted(model, spec, week, prior, items, moved_current, moved_prior, stores) -> list[str]:
    measures_text = "; ".join(
        f"{model.measure(m)['label']}: {model.measure(m)['formula']}" for m in spec["measures"]
    )
    ranked = ""
    if len(spec["measures"]) > 1:
        ranked = f" Largest increases are ranked by {model.measure(spec['measure'])['label']}."
    lines = [
        "Computed from the Meridian extract. The measure, departments, and dates come from the team and week you selected.",
        f"Team: {spec['team_label']}. Measures: {measures_text}.{ranked}",
        (
            f"Selected week counted: {week.counted_start.isoformat()} through {week.counted_end.isoformat()} "
            f"({week.days} {_day_word(week.days)}). "
            f"Monday–Sunday week: {week.start.isoformat()} through {week.end.isoformat()}."
        ),
    ]
    if prior.available:
        lines.append(
            f"Prior week counted: {prior.counted_start.isoformat()} through {prior.counted_end.isoformat()} "
            f"({prior.days} {_day_word(prior.days)}). "
            f"Monday–Sunday week: {prior.start.isoformat()} through {prior.end.isoformat()}."
        )
    else:
        lines.append(
            f"Prior week {prior.start.isoformat()} through {prior.end.isoformat()} is outside the extract "
            f"({model.window_start.isoformat()} through {model.window_end.isoformat()}). "
            "It is omitted, not shown as zero."
        )
    lines.append("Departments: " + ", ".join(spec["departments"]) + ".")
    item_line = (
        f"Items: {len(items)} item ids in those departments. "
        f"{moved_current} had a shipment or a sale in the selected week"
    )
    if prior.available:
        item_line += f", and {moved_prior} in the prior week"
    item_line += ". Inclusion is by item_id. The description is only a label."
    lines.append(item_line)
    store_ids = ", ".join(str(row["store_id"]) for row in stores)
    if spec["by_store"]:
        lines.append(
            f"Stores: {store_ids}. Each store is totaled on its own. "
            "Banner and region are that store's values on the extract."
        )
    else:
        lines.append(f"Stores: {store_ids}. Figures are summed across these stores.")
    if prior.available:
        lines.append("Change is the selected week minus the prior week, on this same population.")
    return lines


def _notes(week: _Week, prior: _Week) -> list[str]:
    notes = []
    if week.partial:
        notes.append(
            "The selected Monday–Sunday week extends outside the extract. "
            "Only the selected-week dates above are included."
        )
    if prior.available and prior.partial:
        notes.append(
            "The prior Monday–Sunday week extends outside the extract. "
            "Only the prior-week dates above are included."
        )
    if prior.available and week.days != prior.days:
        notes.append(
            f"The selected week contains {week.days} {_day_word(week.days)} and the prior week contains "
            f"{prior.days} {_day_word(prior.days)}. The change compares those dates as counted, not two full weeks."
        )
    if not prior.available:
        notes.append("There is no prior-week column and no increase ranking, because that week is not in the extract.")
    return notes


def _rank_note(count: int, comparable: bool) -> str:
    if not comparable:
        return "Increase is not ranked. The prior week is outside the extract, so there is no week to subtract."
    if count == 0:
        return "No item increased versus the prior week."
    if count < 10:
        noun = "item" if count == 1 else "items"
        return (
            f"{count} {noun} increased versus the prior week, so fewer than ten are listed. "
            "Largest increase first. Item id breaks a tie."
        )
    return "The ten largest increases, selected week minus the prior week. Item id breaks a tie."


def _total(rows: list[dict], comparable: bool, digits: int) -> dict:
    current = _num(sum(row["current"] for row in rows), digits)
    if not comparable:
        return {"current": current, "prior": None, "change": None}
    prior = _num(sum(row["prior"] for row in rows), digits)
    return {"current": current, "prior": prior, "change": _num(current - prior, digits)}


def _pack(row: dict, spec: dict, model, comparable: bool, measure: str) -> dict:
    packed = _values(row[f"{measure}_current"], row.get(f"{measure}_prior"), comparable, _digits(measure))
    if measure != spec["measure"]:
        packed = {"measure": measure, "unit": model.measure(measure)["unit"], **packed}
    return packed


def _with_also(packed: dict, row: dict, spec: dict, model, comparable: bool) -> dict:
    if len(spec["measures"]) > 1:
        packed["also"] = _pack(row, spec, model, comparable, spec["measures"][1])
    return packed


def _total_with_also(rows: list[dict], spec: dict, model, comparable: bool) -> dict:
    total = _total(rows, comparable, _digits(spec["measure"]))
    if len(spec["measures"]) > 1:
        other = spec["measures"][1]
        also_total = _total([row["also"] for row in rows], comparable, _digits(other))
        total["also"] = {"measure": other, "unit": model.measure(other)["unit"], **also_total}
    return total


def _item_row(row: dict, spec: dict, model, comparable: bool) -> dict:
    packed = _pack(row, spec, model, comparable, spec["measure"])
    packed = _with_also(packed, row, spec, model, comparable)
    return {
        "item_id": int(row["item_id"]),
        "description": row["description"],
        "dept": row["dept"],
        **packed,
    }


def _movement(con, departments: list[str], start: date, end: date) -> int:
    depts = ", ".join(_q(d) for d in departments)
    sql = f"""
        SELECT COUNT(DISTINCT f.item_id)
        FROM fact f
        JOIN items i ON i.item_id = f.item_id
        WHERE i.dept IN ({depts})
          AND f.date BETWEEN DATE {_q(start.isoformat())} AND DATE {_q(end.isoformat())}
          AND (f.cases_received <> 0 OR f.units_sold <> 0)
    """
    return int(con.execute(sql).fetchone()[0])


_BASIS_MEASURES = {"dollars": "shrink_cost", "units": "shrink_units"}


def build_report(con, model: SemanticModel, team: str, week_start: date, basis: str | None = None) -> dict:
    """Build one weekly report. `week_start` is the Monday of a Monday–Sunday week.

    The team sets the population and the default measure. `basis` is the
    reader's view: dollars, units, or both. In both, the team's measure
    stays first and ranks the item list.
    """
    spec = _team_spec(model, team)
    team_measure = spec["measure"]
    choice = (basis or "").strip().lower()
    if choice in ("", "team"):
        spec["measures"] = [team_measure]
        effective = "dollars" if team_measure == "shrink_cost" else "units"
    elif choice in _BASIS_MEASURES:
        spec["measure"] = _BASIS_MEASURES[choice]
        spec["measures"] = [spec["measure"]]
        effective = choice
    elif choice == "both":
        other = "shrink_units" if team_measure == "shrink_cost" else "shrink_cost"
        spec["measures"] = [team_measure, other]
        effective = "both"
    else:
        raise ReportError("Choose dollars, units, or both for the view.")
    spec["basis"] = effective
    week = _require_week(model, week_start)
    prior = _prior(week, model.window_start, model.window_end)
    comparable = prior.available
    measure = model.measure(spec["measure"])

    dept_sql = _dept_sql(spec, week, prior)
    dept_rows = _query(con, dept_sql)
    top_sql = _top_sql(spec, week, prior) if comparable else None
    top_rows = _query(con, top_sql) if top_sql else []

    depts = ", ".join(_q(d) for d in spec["departments"])
    items = _query(
        con,
        f"""
        SELECT item_id, description, dept
        FROM items
        WHERE dept IN ({depts})
        ORDER BY item_id
        """,
    )
    order = {name: i for i, name in enumerate(spec["departments"])}
    included = [
        {"item_id": int(row["item_id"]), "description": row["description"], "dept": row["dept"]}
        for row in sorted(items, key=lambda row: (order[row["dept"]], int(row["item_id"])))
    ]
    stores = _query(
        con,
        """
        SELECT store_id, MIN(banner) AS banner, MIN(region) AS region
        FROM fact
        GROUP BY store_id
        ORDER BY store_id
        """,
    )
    stores = [
        {"store_id": int(row["store_id"]), "banner": row["banner"], "region": row["region"]}
        for row in stores
    ]
    moved_current = _movement(con, spec["departments"], week.counted_start, week.counted_end)
    moved_prior = (
        _movement(con, spec["departments"], prior.counted_start, prior.counted_end) if comparable else 0
    )

    report = {
        "team": spec["team"],
        "team_label": spec["team_label"],
        "measure": spec["measure"],
        "measures": list(spec["measures"]),
        "basis": effective,
        "ranked_by": spec["measure"],
        "measure_label": measure["label"],
        "unit": measure["unit"],
        "formula": measure["formula"],
        "departments": list(spec["departments"]),
        "week": week.as_dict(),
        "prior_week": prior.as_dict(),
        "comparable": comparable,
        "counted": _counted(model, spec, week, prior, included, moved_current, moved_prior, stores),
        "notes": _notes(week, prior),
        "included_items": included,
        "sql": {"departments": dept_sql, "top_items": top_sql},
        "departments_table": [],
        "department_total": None,
        "top_items": [],
        "top_items_note": "",
        "stores": [],
    }

    if spec["by_store"]:
        by_store: dict[int, dict] = {}
        for row in dept_rows:
            store_id = int(row["store_id"])
            entry = by_store.setdefault(
                store_id,
                {
                    "store_id": store_id,
                    "banner": row["banner"],
                    "region": row["region"],
                    "departments": [],
                    "top_items": [],
                },
            )
            packed = _pack(row, spec, model, comparable, spec["measure"])
            entry["departments"].append({
                "dept": row["dept"],
                **_with_also(packed, row, spec, model, comparable),
            })
        for row in top_rows:
            by_store[int(row["store_id"])]["top_items"].append(_item_row(row, spec, model, comparable))
        for entry in by_store.values():
            entry["department_total"] = _total_with_also(entry["departments"], spec, model, comparable)
            entry["top_items_note"] = _rank_note(len(entry["top_items"]), comparable)
        report["stores"] = [by_store[store["store_id"]] for store in stores]
    else:
        report["departments_table"] = [
            {"dept": row["dept"], **_with_also(_pack(row, spec, model, comparable, spec["measure"]), row, spec, model, comparable)}
            for row in dept_rows
        ]
        report["department_total"] = _total_with_also(report["departments_table"], spec, model, comparable)
        report["top_items"] = [_item_row(row, spec, model, comparable) for row in top_rows]
        report["top_items_note"] = _rank_note(len(report["top_items"]), comparable)
    return report
