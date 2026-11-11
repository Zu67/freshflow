"""Orchestration: question in, answer card out."""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from . import checks, llm, narrator, observe, planner
from .compiler import compile_sql, run
from .semantic import SemanticModel, load
from .spec import QuerySpec
from .warehouse import connect


@dataclass
class AnswerCard:
    question: str
    answerable: bool
    answer: str = ""
    restatement: str = ""
    assumptions: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    sql: str = ""
    compare_sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    compare_rows: list[dict] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    sensitivity: dict | None = None
    scope_note: dict | None = None
    unverified_figures: list[str] = field(default_factory=list)
    spec: dict | None = None
    elapsed_s: float = 0.0
    backend: str = ""
    error: bool = False
    column_units: dict[str, str] = field(default_factory=dict)
    column_labels: dict[str, str] = field(default_factory=dict)
    headline: dict | None = None
    trace: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Engine:
    def __init__(self, extract_dir=None, semantic_path=None):
        self.con = connect(extract_dir)
        self.model: SemanticModel = load(semantic_path) if semantic_path else load()

    # -- assumptions are built from the spec, never written by the model ----
    def _assumptions(self, spec: QuerySpec) -> list[str]:
        m = self.model
        out = [
            f"Dates: {spec.time.start} through {spec.time.end}"
            + (f" ({spec.time.label})" if spec.time.label else "")
            + "."
        ]
        if spec.compare_time:
            out.append(
                f"Compared with {spec.compare_time.start} through {spec.compare_time.end}"
                + (f" ({spec.compare_time.label})" if spec.compare_time.label else "")
                + "."
            )
        if spec.scope == "fresh":
            out.append(
                "Grocery is left out. This is Produce, Dairy, Meat, Bakery, and Deli only."
            )
        elif spec.scope == "all":
            out.append("Every department is included, Grocery too.")
        else:
            out.append("Only the departments named in the question.")
        for f in spec.filters:
            label = m.dimension(f.field)["label"]
            verb = {"in": "is", "not_in": "is not", "eq": "is", "contains": "name contains"}[f.op]
            out.append(f"{label} {verb} {', '.join(f.values)}.")
        if spec.dimensions:
            out.append("Broken out by " + ", ".join(m.dimension(d)["label"] for d in spec.dimensions) + ".")
        if spec.dimensions and spec.order_by:
            out.append(
                f"Sorted by {m.measures.get(spec.order_by, {}).get('label', spec.order_by)}, "
                f"{'largest' if spec.order_desc else 'smallest'} first, {spec.limit} rows."
            )
        return out

    def _catalog_fact(self, question: str) -> tuple[str, str, str, str] | None:
        """A count or a date range the extract already states. Anything else goes to the planner."""
        kind = _fact_kind(question)
        if kind == "stores":
            sql = "SELECT COUNT(DISTINCT store_id) FROM fact"
            n = self.con.execute(sql).fetchone()[0]
            return ("stores", f"There are {n} stores in this extract.", _counted(self.model, "Stores"), sql)
        if kind == "items":
            sql = "SELECT COUNT(*) FROM items"
            n = self.con.execute(sql).fetchone()[0]
            return ("items", f"There are {n} items in this extract.", _counted(self.model, "Items"), sql)
        if kind == "window":
            start, end = self.model.window_start, self.model.window_end
            span = (
                f"{start.strftime('%B')} {start.day} through {end.strftime('%B')} {end.day}, {end.year}"
                if start.year == end.year
                else f"{start.strftime('%B')} {start.day}, {start.year} through {end.strftime('%B')} {end.day}, {end.year}"
            )
            return (
                "window",
                f"This extract runs from {span}.",
                "The dates the extract was built to cover.",
                "",
            )
        return None

    def _sources(self, card: AnswerCard, spec: QuerySpec) -> list[dict]:
        """A few labeled citations. They sit under the answer, not in the sentence."""
        items: list[dict] = []

        def add(label: str, text: str) -> None:
            cleaned = " ".join(text.split())
            if cleaned and not cleaned.endswith("."):
                cleaned += "."
            items.append({"n": len(items) + 1, "label": label, "text": cleaned})

        when = f"{spec.time.start.isoformat()} through {spec.time.end.isoformat()}"
        if spec.time.label:
            when = f"{spec.time.label}, {when}"
        counted = when
        if spec.compare_time:
            prior = f"{spec.compare_time.start.isoformat()} through {spec.compare_time.end.isoformat()}"
            if spec.compare_time.label:
                prior = f"{spec.compare_time.label}, {prior}"
            counted += f", compared with {prior}"
        if spec.scope == "fresh":
            counted += ". Produce, Dairy, Meat, Bakery, and Deli. Grocery is left out"
        elif spec.scope == "all":
            counted += ". Every department is included, Grocery too"
        else:
            counted += ". Only the departments named in the question"
        for filt in spec.filters:
            label = self.model.dimension(filt.field)["label"]
            verb = {"in": "is", "not_in": "is not", "eq": "is", "contains": "name contains"}[filt.op]
            counted += f". {label} {verb} {', '.join(filt.values)}"
        if spec.dimensions:
            dims = ", ".join(self.model.dimension(d)["label"].lower() for d in spec.dimensions)
            counted += f". Split by {dims}"
        add("Counted", counted)

        formulas = [self.model.describe(m) for m in spec.measures]
        add("Formula", " ".join(formulas))

        if card.scope_note:
            note = card.scope_note
            fresh = narrator.format_measure(note["fresh_total"], note["unit"])
            everything = narrator.format_measure(note["all_total"], note["unit"])
            departments = ", ".join(note["departments_excluded"])
            if note["delta_pct"] is None:
                tail = "(no percentage: the fresh total is zero)."
            else:
                tail = f"({note['delta_pct']:+.1f}%)."
            add("Grocery", f"Fresh only: {fresh}. Including {departments}: {everything} {tail}")
        if card.caveats:
            add("Check", " ".join(card.caveats))
        return items

    def ask(self, question: str, basis: str = "auto") -> AnswerCard:
        trace = observe.Trace("ask", question=question)
        t0 = time.time()
        card = AnswerCard(question=question, answerable=False, backend=llm.backend_name())
        fact = self._catalog_fact(question)
        if fact is not None:
            trace.step("catalog", fact=fact[0])
            card.answerable = True
            card.answer = fact[1]
            card.headline = {
                "parts": [{"text": fact[1], "tone": ""}],
                "table": None,
                "sources": [{"n": 1, "label": "Counted", "text": fact[2]}],
                "read": "",
                "notes": [],
            }
            card.sql = fact[3]
            return self._finish(card, trace, t0)

        try:
            result = planner.plan(question, self.model)
        except llm.LLMError as exc:
            card.answer = str(exc)
            card.error = True
            trace.fail(str(exc))
            return self._finish(card, trace, t0)
        trace.step("plan", answerable=bool(result.answerable and result.spec))
        card.restatement = result.restatement
        if not result.answerable or result.spec is None:
            card.answer = result.reason
            stores = self.con.execute("SELECT COUNT(DISTINCT store_id) FROM fact").fetchone()[0]
            items = self.con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            card.caveats = [
                "FreshFlow answers questions about sales and shrink in this extract. "
                f"It covers {self.model.window_start.isoformat()} through {self.model.window_end.isoformat()}, "
                f"with {stores} stores and {items} items."
            ]
            return self._finish(card, trace, t0)

        spec = result.spec
        _align_item_filters(question, spec)
        _apply_basis(self.model, spec, basis)
        trace.step("basis", basis=basis, measures=",".join(spec.measures))
        card.answerable = True
        card.spec = spec.model_dump(mode="json")
        card.assumptions = self._assumptions(spec)
        card.definitions = [self.model.describe(m) for m in spec.measures]
        card.column_units = {m: self.model.measure(m)["unit"] for m in spec.measures}
        card.column_labels = {d: self.model.dimension(d)["label"] for d in spec.dimensions}
        card.column_labels.update({m: narrator.spoken(self.model, m) for m in spec.measures})

        card.sql, card.columns, card.rows = run(self.con, spec, self.model)
        if spec.compare_time:
            card.compare_sql = compile_sql(spec, self.model, spec.compare_time)
            _, _, card.compare_rows = run(self.con, spec, self.model, spec.compare_time)
        trace.step("query", rows=len(card.rows), compare_rows=len(card.compare_rows))

        card.sensitivity = checks.basis_sensitivity(self.con, spec, self.model, card.rows)
        card.scope_note = checks.scope_sensitivity(self.con, spec, self.model)
        card.caveats = checks.caveats(self.con, spec, self.model, card.rows)
        trace.step("checks", caveats=len(card.caveats))

        card.answer, card.headline = narrator.compose(
            self.model, spec, card.rows, card.compare_rows
        )
        both_bases = any(
            self.model.paired_measure(m) in spec.measures for m in spec.measures
        )
        note = (card.sensitivity or {}).get("message") if (card.sensitivity or {}).get("disagrees") else ""
        if note and not both_bases:
            card.headline["note"] = note
            card.answer = card.answer + " " + note
        card.headline["sources"] = self._sources(card, spec)
        card.headline["read"] = card.restatement
        card.headline["notes"] = [n for n in result.notes if n]
        if card.rows and re.search(r"\bwhy\b", question, re.IGNORECASE):
            clause = "The numbers show what moved, but they don't say what caused it."
            card.headline["parts"].append({"text": " " + clause, "tone": ""})
            card.answer = card.answer + " " + clause
        trace.step("narrate", chars=len(card.answer))

        extra = _sensitivity_numbers(card.sensitivity) + _row_numbers(card.compare_rows)
        labels = [v for f in spec.filters for v in f.values]
        card.unverified_figures = checks.verify_numbers(card.answer, card.rows, extra, labels)
        if card.unverified_figures:
            trace.step("verify", unverified=len(card.unverified_figures))
        return self._finish(card, trace, t0)

    def _finish(self, card: AnswerCard, trace: observe.Trace, started: float) -> AnswerCard:
        card.elapsed_s = round(time.time() - started, 2)
        card.trace = trace.finish(answerable=card.answerable, backend=card.backend, failed=card.error)
        return card


_FILLER = re.compile(
    r"\b(are there|is there|do we have|do you have|do we operate|do you operate|"
    r"in the extract|in this extract|in the data|in total|please|tell me|currently)\b"
)
_TOPIC = re.compile(
    r"\b(shrink|sale|sales|sold|dollar|dollars|unit|units|department|dept|week|month|"
    r"produce|dairy|meat|bakery|deli|grocery)\b"
)


def _fact_kind(question: str) -> str | None:
    text = re.sub(r"[^a-z0-9\s]", " ", question.lower())
    text = " ".join(_FILLER.sub(" ", text).split())
    if _TOPIC.search(text):
        return None
    if re.search(r"\b(how many stores|number of stores|store count|count of stores)\b", text):
        return "stores"
    if re.search(r"\b(how many items|how many products|number of items|number of products|item count|product count)\b", text):
        return "items"
    if re.search(r"\b(date range|what dates|which dates|what period|time period)\b", text):
        return "window"
    return None


def _apply_basis(model: SemanticModel, spec, basis: str) -> None:
    """Apply the reader's dollars/units/both choice to shrink measures.

    The planner still reads the question. This only swaps a shrink measure
    for its pair, or adds the pair, so the choice is deterministic.
    """
    choice = (basis or "auto").lower()
    if choice not in ("dollars", "units", "both"):
        choice = "auto"
    if choice == "auto":
        return
    measures: list[str] = []
    for measure in spec.measures:
        pair = model.paired_measure(measure)
        if not pair:
            candidates = [measure]
        elif choice == "both":
            candidates = [measure, pair]
        else:
            target = "cost" if choice == "dollars" else "units"
            chosen = measure if measure.endswith(target) else pair
            candidates = [chosen]
        for candidate in candidates:
            if candidate not in measures:
                measures.append(candidate)
    spec.measures = measures
    if spec.order_by:
        pair = model.paired_measure(spec.order_by)
        if pair and choice != "both":
            target = "cost" if choice == "dollars" else "units"
            if not spec.order_by.endswith(target):
                spec.order_by = pair


def _align_item_filters(question: str, spec) -> None:
    """Keep the word the person typed when the planner shortens it."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", question)
    for f in spec.filters:
        if f.field != "item" or f.op != "contains":
            continue
        aligned = []
        for value in f.values:
            matches = [w for w in words if w.lower().startswith(value.lower()) and len(w) >= len(value)]
            aligned.append(max(matches, key=len) if matches else value)
        f.values = aligned


def _counted(model: SemanticModel, label: str) -> str:
    return (
        f"{label} in the Meridian extract, "
        f"{model.window_start.isoformat()} through {model.window_end.isoformat()}."
    )


def _row_numbers(rows: list[dict]) -> list[float]:
    return [float(v) for r in rows for v in r.values() if isinstance(v, (int, float))]


def _sensitivity_numbers(s: dict | None) -> list[float]:
    if not s:
        return []
    return [float(v) for v in s.values() if isinstance(v, (int, float))]
