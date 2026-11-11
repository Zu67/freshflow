"""FastAPI server. Question answering, plus the weekly shrink report."""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .engine import Engine
from .env import load_env
from . import observe
from .report import ReportError, build_report, list_weeks
from .semantic import load

STATIC = Path(__file__).resolve().parents[2] / "static"

load_env()
observe.configure()
app = FastAPI(title="FreshFlow Analytics")
engine = Engine()

EXAMPLES = [
    "What were our top 10 items by shrink last month?",
    "Is shrink up or down versus the prior month?",
    "Which stores have the worst shrink?",
    "Why is dairy shrink up in the Northeast in June?",
    "Which department has the highest shrink rate?",
    "Which items have high unit shrink but little cost impact?",
    "How did strawberry sales trend over the three months?",
    "What are our best-selling items in Produce?",
    "How do our two banners compare on shrink?",
    "What was our profit margin by department?",
]


class Ask(BaseModel):
    question: str
    basis: Literal["auto", "dollars", "units", "both"] = "auto"


@app.middleware("http")
async def access_log(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        observe.logger.exception("request failed %s %s", request.method, request.url.path)
        raise
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    observe.logger.info("http %s %s %s %sms", request.method, request.url.path, response.status_code, elapsed)
    return response


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta():
    m = load()
    return {
        "examples": EXAMPLES,
        "window": [m.dataset["window_start"], m.dataset["window_end"]],
        "measures": {k: v["formula"] for k, v in m.measures.items()},
        "fresh": m.fresh_departments,
    }


@app.post("/api/ask")
def ask(body: Ask):
    return engine.ask(body.question, body.basis).to_dict()


@app.get("/api/report/weeks")
def report_weeks():
    return {"weeks": list_weeks(engine.model)}


@app.get("/api/report")
def report(team: str, week: str, basis: str | None = None):
    trace = observe.Trace("report", team=team, week=week, basis=basis or "team")
    try:
        week_start = date.fromisoformat(week)
    except ValueError:
        trace.fail("week must be YYYY-MM-DD")
        trace.finish()
        raise HTTPException(status_code=400, detail="week must be YYYY-MM-DD, the Monday of the week.") from None
    try:
        built = build_report(engine.con, engine.model, team, week_start, basis)
    except ReportError as exc:
        trace.fail(str(exc))
        trace.finish()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    trace.step(
        "build",
        departments=len(built.get("departments_table") or []),
        stores=len(built.get("stores") or []),
        items=len(built.get("top_items") or []),
    )
    built["trace"] = trace.finish(comparable=built["comparable"])
    return built


@app.get("/api/debug")
def debug_recent():
    return {"log": str(observe.LOG_FILE), "recent": observe.recent()}
