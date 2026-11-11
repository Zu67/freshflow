"""Terminal front end: `uv run freshflow "your question"`."""
from __future__ import annotations

import sys
from datetime import date

from .engine import Engine
from .env import load_env
from .report import ReportError, build_report


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _fmt_measure(value, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "dollars":
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):,.2f}"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    def line(cells: list[str]) -> str:
        return "  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))
    print(line(headers))
    print(line(["-" * width for width in widths]))
    for row in rows:
        print(line(row))
    print()


def _also_headers(rows: list[dict], current: str, prior: str) -> tuple[list[str], str | None]:
    if rows and rows[0].get("also"):
        unit = rows[0]["also"]["unit"]
        tag = "$" if unit == "dollars" else "units"
        return [f"{current} ({tag})", f"{prior} ({tag})", f"Change ({tag})"], unit
    return [], None


def _also_cells(row: dict) -> list[str]:
    also = row.get("also")
    if not also:
        return []
    return [_fmt_measure(also["current"], also["unit"]), _fmt_measure(also["prior"], also["unit"]), _fmt_measure(also["change"], also["unit"])]


def _print_departments(title: str, rows: list[dict], total: dict, unit: str, current: str, prior: str) -> None:
    print(title)
    extra, _ = _also_headers(rows, current, prior)
    body = [
        [row["dept"], _fmt_measure(row["current"], unit), _fmt_measure(row["prior"], unit), _fmt_measure(row["change"], unit)] + _also_cells(row)
        for row in rows
    ]
    body.append(
        ["Total", _fmt_measure(total["current"], unit), _fmt_measure(total["prior"], unit), _fmt_measure(total["change"], unit)] + _also_cells(total)
    )
    _print_table(["Department", current, prior, "Change"] + extra, body)


def _print_items(title: str, rows: list[dict], note: str, unit: str, current: str, prior: str) -> None:
    print(title)
    print("  " + note)
    if not rows:
        print()
        return
    extra, _ = _also_headers(rows, current, prior)
    body = [
        [
            str(row["item_id"]),
            row["description"],
            row["dept"],
            _fmt_measure(row["current"], unit),
            _fmt_measure(row["prior"], unit),
            _fmt_measure(row["change"], unit),
        ] + _also_cells(row)
        for row in rows
    ]
    _print_table(["Item ID", "Item", "Department", current, prior, "Change"] + extra, body)


def _print_report(report: dict) -> None:
    unit = report["unit"]
    current = report["week"]["label"]
    prior = report["prior_week"]["label"] if report["comparable"] else "Prior week"
    print(f"\n{report['team_label']} weekly report\n")
    print("WHAT WAS COUNTED")
    for line in report["counted"]:
        print("  - " + line)
    print()
    if report["notes"]:
        print("NOTES")
        for line in report["notes"]:
            print("  - " + line)
        print()
    if report["team"] == "finance":
        _print_departments("ALL STORES", report["departments_table"], report["department_total"], unit, current, prior)
        _print_items("LARGEST INCREASES, CHAIN-WIDE", report["top_items"], report["top_items_note"], unit, current, prior)
    else:
        for store in report["stores"]:
            title = f"STORE {store['store_id']} · {store['banner']} · {store['region']}"
            _print_departments(title, store["departments"], store["department_total"], unit, current, prior)
            _print_items("LARGEST INCREASES", store["top_items"], store["top_items_note"], unit, current, prior)
    print("SQL")
    print(report["sql"]["departments"])
    if report["sql"]["top_items"]:
        print()
        print(report["sql"]["top_items"])
    print()


def _report_cli(args: list[str]) -> int:
    if len(args) != 2 or args[0] not in ("finance", "ops"):
        print('usage: freshflow report <finance|ops> <monday YYYY-MM-DD>')
        return 2
    try:
        week = date.fromisoformat(args[1])
    except ValueError:
        print("week must be YYYY-MM-DD, the Monday of the week.")
        return 2
    engine = Engine()
    try:
        report = build_report(engine.con, engine.model, args[0], week)
    except ReportError as exc:
        print(str(exc))
        return 1
    _print_report(report)
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: freshflow "your question"')
        print("       freshflow report <finance|ops> <monday YYYY-MM-DD>")
        return 2
    load_env()
    if sys.argv[1] == "report":
        return _report_cli(sys.argv[2:])
    card = Engine().ask(" ".join(sys.argv[1:]))
    print(f"\nQ: {card.question}\n")
    print(card.answer + "\n")
    sources = (card.headline or {}).get("sources") or []
    if sources:
        print("Sources")
        for source in sources:
            print(f"  {source.get('label', source['n'])}: {source['text']}")
        print()
    if card.assumptions:
        print("ASSUMPTIONS")
        for a in card.assumptions:
            print("  - " + a)
        print()
    if card.rows:
        cols = list(card.rows[0].keys())
        print("  " + " | ".join(cols))
        for r in card.rows[:15]:
            print("  " + " | ".join(_fmt(r[c]) for c in cols))
        print()
    if card.caveats:
        print("CAVEATS")
        for c in card.caveats:
            print("  - " + c)
        print()
    if card.unverified_figures:
        print("UNVERIFIED FIGURES (not found in the rows): " + ", ".join(card.unverified_figures) + "\n")
    if card.sql:
        print("SQL\n" + "\n".join("  " + l for l in card.sql.splitlines()) + "\n")
    print(f"[{card.backend}, {card.elapsed_s}s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
