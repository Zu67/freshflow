"""The debug trace records each step and keeps a short recent list."""
from freshflow import observe


def test_trace_records_steps_and_lands_in_the_recent_list(tmp_path, monkeypatch):
    monkeypatch.setattr(observe, "LOG_FILE", tmp_path / "freshflow.log")
    monkeypatch.setattr(observe, "LOG_DIR", tmp_path)
    trace = observe.Trace("ask", question="June shrink")
    trace.step("plan", answerable=True)
    trace.step("query", rows=3)
    done = trace.finish(answerable=True)
    assert done["id"] == trace.id
    assert [step["name"] for step in done["steps"]] == ["plan", "query"]
    assert done["steps"][0]["ms"] >= 0
    assert observe.recent(5)[0]["id"] == trace.id
    assert "June shrink" in observe.recent(1)[0]["fields"]["question"]


def test_a_failed_trace_keeps_the_error():
    trace = observe.Trace("report", team="finance", week="nope")
    trace.fail("week must be YYYY-MM-DD")
    done = trace.finish()
    assert done["error"] == "week must be YYYY-MM-DD"
    assert observe.LOG_FILE.name == "freshflow.log"
