"""Compiles a validated QuerySpec into DuckDB SQL. Pure code, no model.

The same spec always produces the same SQL, so an analyst can paste the SQL
shown on an answer card and reproduce the number.
"""
from __future__ import annotations

import re

from .semantic import SemanticModel
from .spec import Filter, QuerySpec, TimeRange


def _quote(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _word_sql(col: str, term: str) -> str:
    """Match a whole word, so apples does not match Apple Juice or Apple Pie."""
    pattern = r"(^|[^a-z0-9])" + re.escape(term.lower()) + r"([^a-z0-9]|$)"
    return f"regexp_matches(lower({col}), {_quote(pattern)})"


def _filter_sql(model: SemanticModel, f: Filter) -> str:
    col = model.dimension(f.field)["sql"]
    if f.op == "contains":
        terms = " OR ".join(_word_sql(col, v) for v in f.values)
        return f"({terms})"
    if f.op == "eq":
        return f"{col} = {_quote(f.values[0])}"
    vals = ", ".join(_quote(v) for v in f.values)
    return f"{col} {'NOT IN' if f.op == 'not_in' else 'IN'} ({vals})"


def scope_filter(model: SemanticModel, scope: str) -> str | None:
    """The department filter implied by the scope policy."""
    if scope == "fresh":
        vals = ", ".join(_quote(v) for v in model.fresh_departments)
        return f"i.dept IN ({vals})"
    return None


def where_sql(spec: QuerySpec, model: SemanticModel, time: TimeRange | None = None) -> str:
    """The same time, scope, and explicit filters for results and checks."""
    tr = time or spec.time
    where = [f"f.date BETWEEN DATE {_quote(tr.start.isoformat())} AND DATE {_quote(tr.end.isoformat())}"]
    sc = scope_filter(model, spec.scope)
    if sc:
        where.append(sc)
    where += [_filter_sql(model, f) for f in spec.filters]
    return "\n  AND ".join(where)


def compile_sql(spec: QuerySpec, model: SemanticModel, time: TimeRange | None = None) -> str:
    """Build the SQL for one time range of a spec."""
    selects, groups = [], []
    for d in spec.dimensions:
        dim = model.dimension(d)
        selects.append(f"{dim['sql']} AS {d}")
        groups.append(dim["sql"])
    for m in spec.measures:
        selects.append(f"{model.measure(m)['sql']} AS {m}")

    sql = [
        "SELECT " + ",\n       ".join(selects),
        "FROM fact f",
        "JOIN items i ON i.item_id = f.item_id",
        "WHERE " + where_sql(spec, model, time),
    ]
    if groups:
        sql.append("GROUP BY " + ", ".join(groups))
        order = spec.order_by or spec.measures[0]
        sql.append(f"ORDER BY {order} {'DESC' if spec.order_desc else 'ASC'} NULLS LAST")
        sql.append(f"LIMIT {spec.limit}")
    return "\n".join(sql)


def run(con, spec: QuerySpec, model: SemanticModel, time: TimeRange | None = None):
    """Execute one time range and return (sql, column names, rows as dicts)."""
    sql = compile_sql(spec, model, time)
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return sql, cols, rows
