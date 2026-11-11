"""Writes the answer from the returned rows. Nothing else.

A single total is one sentence. A breakdown is that sentence for the first
rows, plus a table of every row. Sources are attached by the engine.
A model does not write the sentence, and it cannot add a figure that was
not returned.
"""
from __future__ import annotations

from .semantic import SemanticModel
from .spec import QuerySpec

# Spoken names. The catalog labels are accurate and hard to say out loud.
_SPOKEN = {
    "shrink_cost": "Shrink in dollars",
    "shrink_units": "Shrink in units",
    "shrink_rate_cost": "Shrink rate in dollars",
    "shrink_rate_units": "Shrink rate in units",
    "units_sold": "Units sold",
    "net_sales": "Net sales",
    "units_shipped": "Units shipped",
    "cost_shipped": "Cost of what was shipped",
}
_WERE = {"Units sold", "Net sales", "Units shipped"}


def spoken(model: SemanticModel, measure: str) -> str:
    if measure in _SPOKEN:
        return _SPOKEN[measure]
    return model.measures.get(measure, {}).get("label", measure)


def format_measure(value, unit: str) -> str:
    """Same rounding the table uses, so the sentence and the table agree."""
    if value is None:
        return "—"
    if unit == "percent":
        return f"{float(value) * 100:.1f}%"
    number = float(value)
    sign = "-" if number < 0 else ""
    text = f"{abs(number):,.2f}".rstrip("0").rstrip(".")
    if unit == "dollars":
        return f"{sign}${text}"
    return f"{sign}{text}"


def compose(model: SemanticModel, spec: QuerySpec, rows: list[dict],
            compare_rows: list[dict]) -> tuple[str, dict]:
    """Return the answer sentence and the table the page shows under it."""
    if not rows:
        parts = [{"text": "Nothing in the extract matches that — try a different item or department name.", "tone": ""}]
        return _text(parts), {"parts": parts, "table": None}

    priors = _priors(spec, compare_rows)
    if spec.dimensions:
        parts = _breakdown(model, spec, rows, priors)
        table = _table(model, spec, rows, priors)
    else:
        parts = _total(model, spec, rows[0], priors.get(()))
        table = None
    return _text(parts), {"parts": parts, "table": table}


def _total(model, spec: QuerySpec, row: dict, prior: dict | None) -> list[dict]:
    parts: list[dict] = []
    for index, measure in enumerate(spec.measures):
        sentence = _total_sentence(model, measure, row, prior, spec, include_period=index == 0)
        if index and sentence:
            sentence[0]["text"] = " " + sentence[0]["text"]
        parts.extend(sentence)
    return parts


def _total_sentence(model, measure: str, row: dict, prior: dict | None,
                    spec: QuerySpec, include_period: bool = True) -> list[dict]:
    label = spoken(model, measure)
    current = row.get(measure)
    before = prior.get(measure) if prior else None
    if current is None:
        return [{"text": f"{label} isn't available for this period.", "tone": ""}]
    unit = model.measure(measure)["unit"]
    move, tone = _move(measure, current, before, unit, spec, include_period)
    when = spec.time.label if include_period and spec.time.label else ""
    opening, after = _total_opening(measure, label, when)
    return [{"text": opening, "tone": ""}] + _figure(format_measure(current, unit), after, move, tone)


def _total_opening(measure: str, label: str, when: str) -> tuple[str, str]:
    """How a person would start the sentence, and what follows the figure."""
    lead = f"In {when}, " if when else ""
    if measure == "shrink_cost":
        return (f"{lead}shrink cost " if when else "Shrink cost "), ""
    if measure == "shrink_units":
        return (f"{lead}shrink came to " if when else "Shrink came to "), " units"
    if measure.startswith("shrink_rate"):
        basis = "by cost" if measure.endswith("cost") else "by units"
        return (f"{lead}the shrink rate ({basis}) was " if when else f"The shrink rate ({basis}) was "), ""
    if measure == "units_sold":
        return lead, " units were sold"
    if measure == "net_sales":
        return (f"{lead}net sales came to " if when else "Net sales came to "), ""
    if measure == "units_shipped":
        return lead, " units were shipped"
    if measure == "cost_shipped":
        return (f"{lead}what was shipped cost " if when else "What was shipped cost "), ""
    return (f"{lead}{_phrase(label)} came to " if when else f"{label} came to "), ""


def _breakdown(model, spec: QuerySpec, rows: list[dict], priors: dict) -> list[dict]:
    measure = spec.measures[0]
    parts = _ranked_sentence(model, spec, measure, rows[0], priors, first=True)
    if len(rows) > 1:
        nxt = _ranked_sentence(model, spec, measure, rows[1], priors, first=False)
        if nxt:
            nxt[0]["text"] = " " + nxt[0]["text"]
        parts.extend(nxt)
    return parts


def _ranked_sentence(model, spec, measure, row, priors, first: bool) -> list[dict]:
    name = _group(spec, row)
    current = row.get(measure)
    prior = priors.get(_key(spec, row))
    before = prior.get(measure) if prior else None
    if current is None:
        return [{"text": f"There's no figure for {name} in this period.", "tone": ""}]
    unit = model.measure(measure)["unit"]
    move, tone = _move(measure, current, before, unit, spec, include_period=first)
    when = spec.time.label if first and spec.time.label else ""
    lead = f"In {when}, " if when else ""
    after = " units" if measure in ("shrink_units", "units_sold", "units_shipped") else ""
    if _ranked(spec):
        if first:
            opening = f"{lead}{name} {_leader(measure)} — "
        else:
            opening = f"{name} was next — "
    else:
        opening = f"{lead}{_unranked_opening(measure, name)}"
    return [{"text": opening, "tone": ""}] + _figure(format_measure(current, unit), after, move, tone)


def _leader(measure: str) -> str:
    if measure.startswith("shrink_rate"):
        return "had the highest shrink rate"
    if measure in ("shrink_cost", "shrink_units"):
        return "had the most shrink"
    if measure == "units_sold":
        return "sold the most"
    if measure == "net_sales":
        return "had the highest net sales"
    if measure == "units_shipped":
        return "shipped the most"
    if measure == "cost_shipped":
        return "had the highest shipping cost"
    return "came first"


def _unranked_opening(measure: str, name: str) -> str:
    if measure.startswith("shrink_rate"):
        return f"{name} had a shrink rate of "
    if measure in ("shrink_cost", "shrink_units"):
        return f"{name} shrink came to "
    if measure == "units_sold":
        return f"{name} sold "
    if measure == "net_sales":
        return f"{name} net sales came to "
    if measure == "units_shipped":
        return f"{name} shipped "
    if measure == "cost_shipped":
        return f"{name} shipping cost came to "
    return f"{name} came to "


def _table(model, spec: QuerySpec, rows: list[dict], priors: dict) -> dict | None:
    if len(rows) < 2:
        return None
    headers = [model.dimension(dim)["label"] for dim in spec.dimensions]
    compared = bool(priors)
    several = len(spec.measures) > 1
    prior_name = "Period before"
    if spec.compare_time and spec.compare_time.label:
        prior_name = spec.compare_time.label
    for measure in spec.measures:
        label = spoken(model, measure)
        current_name = spec.time.label or label
        headers.append(f"{label}, {current_name}" if several and spec.time.label else current_name)
        if compared:
            headers.append(f"{label}, {prior_name}" if several else prior_name)
            if not several:
                headers.append("Change")
    body = []
    for row in rows:
        prior = priors.get(_key(spec, row))
        cells: list[dict] = [{"text": _group_piece(dim, row.get(dim))} for dim in spec.dimensions]
        for measure in spec.measures:
            unit = model.measure(measure)["unit"]
            current = row.get(measure)
            cells.append({"value": current, "unit": unit, "tone": ""})
            if not compared:
                continue
            before = prior.get(measure) if prior else None
            cells.append({"value": before, "unit": unit, "tone": ""})
            if len(spec.measures) == 1:
                cells.append(_change(measure, current, before, unit))
        body.append(cells)
    return {"headers": headers, "rows": body}


def _change(measure: str, current, before, unit: str) -> dict:
    if current is None or before is None or unit == "percent":
        return {"text": "—"}
    delta = round(float(current) - float(before), 2)
    return {"value": delta, "unit": unit, "tone": _tone(measure, current, before)}


def _figure(shown: str, after: str, move: str, tone: str) -> list[dict]:
    parts = [{"text": shown, "tone": "", "fig": True}]
    if after:
        parts.append({"text": after, "tone": ""})
    if not move:
        parts.append({"text": ".", "tone": ""})
        return parts
    parts.append({"text": ", ", "tone": ""})
    parts.append({"text": move, "tone": tone})
    return parts


def _prior_when(spec: QuerySpec, include_period: bool) -> str:
    if not include_period:
        return ""
    if spec.compare_time and spec.compare_time.label:
        return f" in {spec.compare_time.label}"
    return " in the period before"


def _move(measure: str, current, before, unit: str, spec: QuerySpec,
          include_period: bool = True) -> tuple[str, str]:
    if before is None or current is None:
        return "", ""
    if float(current) == float(before):
        return f"the same as{_prior_when(spec, include_period)}.", ""
    word = "up" if float(current) > float(before) else "down"
    return f"{word} from {format_measure(before, unit)}{_prior_when(spec, include_period)}.", _tone(measure, current, before)


def _tone(measure: str, current, before) -> str:
    """Red means more shrink. A sales increase stays uncolored."""
    if before is None or current is None or "shrink" not in measure:
        return ""
    if float(current) == float(before):
        return ""
    return "up" if float(current) > float(before) else "down"


def _ranked(spec: QuerySpec) -> bool:
    return bool(spec.order_desc) and (spec.order_by is None or spec.order_by == spec.measures[0])


def _phrase(label: str) -> str:
    return label[0].lower() + label[1:] if label else label


def _group(spec: QuerySpec, row: dict) -> str:
    parts = [_group_piece(dim, row.get(dim)) for dim in spec.dimensions if row.get(dim) is not None]
    return ", ".join(parts) if parts else "This row"


def _group_piece(dim: str, value) -> str:
    if value is None:
        return "—"
    text = str(value)
    if dim == "store" and text.isdigit():
        return f"Store {text}"
    return text


def _priors(spec: QuerySpec, compare_rows: list[dict]) -> dict:
    if not compare_rows:
        return {}
    if not spec.dimensions:
        return {(): compare_rows[0]}
    return {_key(spec, row): row for row in compare_rows}


def _key(spec: QuerySpec, row: dict) -> tuple:
    return tuple(str(row.get(dim)) for dim in spec.dimensions)


def _text(parts: list[dict]) -> str:
    return "".join(part["text"] for part in parts)
