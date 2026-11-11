"""Trust checks. All deterministic — no model output reaches these.

Two families:
  * sensitivity — re-runs the question under the other defensible definition
    and reports whether the answer changes.
  * caveats — rules that fire on the resolved spec and the returned rows.
"""
from __future__ import annotations

import re
from datetime import timedelta

from .compiler import compile_sql, run, where_sql
from .semantic import SemanticModel
from .spec import QuerySpec, TimeRange

SHORT_WINDOW_DAYS = 14


def _total(con, spec: QuerySpec, model: SemanticModel, measure: str, time: TimeRange) -> float:
    flat = spec.model_copy(update={"measures": [measure], "dimensions": [], "order_by": None})
    _, _, rows = run(con, flat, model, time)
    return float(rows[0][measure]) if rows and rows[0][measure] is not None else 0.0


def _pct_change(now: float, before: float) -> float | None:
    if before == 0:
        return None
    return (now - before) / abs(before) * 100.0


def basis_sensitivity(con, spec: QuerySpec, model: SemanticModel, rows: list[dict]) -> dict | None:
    """Answer the same question on the other basis: units instead of cost, or vice versa.

    Finance thinks in dollars and store ops in units. Reporting one basis
    without the other hides real disagreement, so every shrink answer carries
    both.
    """
    primary = next((m for m in spec.measures if model.paired_measure(m)), None)
    if not primary:
        return None
    other = model.paired_measure(primary)
    out = {
        "primary": primary,
        "primary_label": model.measures[primary]["label"],
        "other": other,
        "other_label": model.measures[other]["label"],
        "primary_definition": model.describe(primary),
        "other_definition": model.describe(other),
        "disagrees": False,
        "message": "",
    }

    p_now = _total(con, spec, model, primary, spec.time)
    o_now = _total(con, spec, model, other, spec.time)
    out["primary_total"], out["other_total"] = p_now, o_now

    if spec.compare_time:
        p_before = _total(con, spec, model, primary, spec.compare_time)
        o_before = _total(con, spec, model, other, spec.compare_time)
        p_chg, o_chg = _pct_change(p_now, p_before), _pct_change(o_now, o_before)
        out.update(primary_change_pct=p_chg, other_change_pct=o_chg)
        if p_chg is not None and o_chg is not None:
            same_sign = (p_chg >= 0) == (o_chg >= 0)
            far_apart = abs(p_chg - o_chg) >= 5.0
            if not same_sign or far_apart:
                out["disagrees"] = True
                out["message"] = (
                    f"{_basis_phrase(primary, capital=True)} {_change_words(p_chg)}, "
                    f"while {_basis_phrase(other)} {_change_words(o_chg)}. "
                    f"The two don't line up here, which usually means the mix of items "
                    f"behind them changed — worth checking those items before acting "
                    f"on either number."
                )
        return out

    if spec.dimensions and rows:
        key = spec.dimensions[0]
        alt = spec.model_copy(update={"measures": [other], "order_by": other})
        _, _, alt_rows = run(con, alt, model)
        top_a = [str(r[key]) for r in rows][: min(10, len(rows))]
        top_b = [str(r[key]) for r in alt_rows][: len(top_a)]
        overlap = len(set(top_a) & set(top_b))
        out.update(alt_ranking=top_b, overlap=overlap, ranked_n=len(top_a))
        if len(top_a) >= 3 and overlap < len(top_a) * 0.7:
            out["disagrees"] = True
            missing = [x for x in top_b if x not in top_a][:3]
            added = f" {_and_list(missing)} would come in." if missing else ""
            out["message"] = (
                f"Ranked by {_basis_phrase(other)} instead, this list changes — "
                f"{_stay_clause(overlap, len(top_a))}.{added}"
            )
    return out


_BASIS_PHRASES = {
    "shrink_cost": "shrink in dollars",
    "shrink_units": "shrink in units",
    "shrink_rate_cost": "the dollar shrink rate",
    "shrink_rate_units": "the unit shrink rate",
}


def _basis_phrase(measure: str, capital: bool = False) -> str:
    phrase = _BASIS_PHRASES.get(measure, measure.replace("_", " "))
    return phrase[0].upper() + phrase[1:] if capital and phrase else phrase


def _change_words(pct: float) -> str:
    if pct > 0:
        return f"rose {abs(pct):.1f}%"
    if pct < 0:
        return f"fell {abs(pct):.1f}%"
    return "stayed flat"


def _stay_clause(overlap: int, total: int) -> str:
    if overlap <= 0:
        return f"none of the {total} names stay"
    if overlap == 1:
        return f"only 1 of the {total} names stays"
    return f"only {overlap} of the {total} names stay"


def _stay_sentence(overlap: int, total: int) -> str:
    if overlap <= 0:
        return f"None of the {total} names stay."
    if overlap == 1:
        return f"Only 1 of the {total} names stays."
    return f"Only {overlap} of the {total} names stay."


def _and_list(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + ", and " + names[-1]


def scope_sensitivity(con, spec: QuerySpec, model: SemanticModel) -> dict | None:
    """What the headline number becomes if Grocery is included."""
    if spec.scope != "fresh":
        return None
    measure = spec.order_by if spec.order_by in spec.measures else spec.measures[0]
    if model.measures[measure]["unit"] == "percent":
        return None
    fresh = _total(con, spec, model, measure, spec.time)
    everything = _total(
        con, spec.model_copy(update={"scope": "all"}), model, measure, spec.time
    )
    delta = everything - fresh
    pct = (delta / fresh * 100.0) if fresh else None
    return {
        "measure": measure,
        "label": model.measures[measure]["label"],
        "unit": model.measures[measure]["unit"],
        "fresh_total": fresh,
        "all_total": everything,
        "delta": delta,
        "delta_pct": pct,
        "departments_excluded": model.center_store_departments,
    }


def caveats(con, spec: QuerySpec, model: SemanticModel, rows: list[dict]) -> list[str]:
    out: list[str] = []
    uses_shrink = any("shrink" in m for m in spec.measures)

    for tr, name in ((spec.time, "requested period"), (spec.compare_time, "comparison period")):
        if tr and (tr.start < model.window_start or tr.end > model.window_end):
            out.append(
                f"Part of the {name} ({tr.start} through {tr.end}) falls outside the extract, "
                f"which runs {model.window_start} through {model.window_end}, "
                f"so only the days we have were counted."
            )

    if uses_shrink:
        days = (spec.time.end - spec.time.start).days + 1
        if days < SHORT_WINDOW_DAYS:
            out.append(
                f"This is only {days} days. A short stretch moves when a delivery lands, "
                f"not only when food is wasted."
            )
        if "date" in spec.dimensions:
            out.append(
                "One day at a time is a bad way to read shrink. Delivery day looks awful "
                "and the next day looks like a gain. Use a week or a month."
            )
        neg = _negative_pairs(con, spec, model)
        if neg:
            out.append(
                f"{neg} store–item pairs sold more than they received in these dates — "
                f"that's stock that arrived earlier selling through, not a gain."
            )

    if not rows:
        out.append("Nothing in the extract matches that. Try a different item or department name.")
    return out


def _negative_pairs(con, spec: QuerySpec, model: SemanticModel) -> int:
    # Count all scoped pairs by identity, independently of the displayed ranking.
    sql = f"""
        SELECT COUNT(*) FROM (
            SELECT f.store_id, f.item_id
            FROM fact f JOIN items i ON i.item_id = f.item_id
            WHERE {where_sql(spec, model)}
            GROUP BY f.store_id, f.item_id
            HAVING {model.measure('shrink_units')['sql']} < 0
        ) negative_pairs
    """
    return con.execute(sql).fetchone()[0]


_NUM = re.compile(r"(?<![\d.-])-?\$?\d+(?:,\d{3})*(?:\.\d+)?%?")


def verify_numbers(prose: str, rows: list[dict], extra: list[float],
                   labels: list[str] | None = None) -> list[str]:
    """Every figure in the narrative must trace to a returned row.

    Returns the figures that do not. The card marks them rather than silently
    trusting the model's arithmetic.
    """
    pool: set[float] = set()
    columns: dict[str, list[float]] = {}
    for r in rows:
        for k, v in r.items():
            if isinstance(v, (int, float)):
                pool.add(float(v))
                columns.setdefault(k, []).append(float(v))
            elif isinstance(v, str):
                # dimension labels that are numeric, such as store ids
                try:
                    pool.add(float(v))
                except ValueError:
                    pass
    pool.update(float(v) for v in extra if v is not None)
    pool |= {abs(v) for v in list(pool)}
    for label in labels or []:
        try:
            pool.add(float(label))
        except ValueError:
            pass
    pool |= _derived(columns, extra)
    pool |= {abs(v) for v in list(pool)}

    unmatched = []
    for token in _NUM.findall(prose):
        raw = token.strip("$%").replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        if "%" not in token and "$" not in token:
            if val == int(val) and 1 <= abs(val) <= 100:
                continue  # small counts like "top 10" are not claims about the data
            if val == int(val) and 1900 <= val <= 2100:
                continue  # a year, not a measurement
        if any(_close(val, p) for p in pool):
            continue
        unmatched.append(token)
    return unmatched


def _derived(columns: dict[str, list[float]], extra: list[float]) -> set[float]:
    """Figures a reader can legitimately work out from the rows.

    A percentage change between two returned values is a fair thing to write, so
    it counts as grounded. A figure that matches neither a row nor one of these
    is flagged.
    """
    out: set[float] = set()
    for values in columns.values():
        vals = values[:25]
        for a in vals:
            for b in vals:
                if b == 0 or a is b:
                    continue
                out.add((a - b) / abs(b) * 100.0)
                out.add(a / b)
        if len(vals) > 1:
            total = sum(vals)
            out.add(total)
            for a in vals:
                if total:
                    out.add(a / total * 100.0)
    return out


def _close(a: float, b: float) -> bool:
    if b == 0:
        return abs(a) < 0.51
    for scale in (1.0, 100.0, 0.01, 0.001):
        if abs(a - b * scale) <= max(abs(b * scale) * 0.01, 0.51):
            return True
    return False
