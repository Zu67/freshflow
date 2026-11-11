# FreshFlow Analytics

Ask plain-language questions about Meridian Markets' sales and shrink. The web app returns an answer card with rows, SQL, definitions, assumptions, and checks. The bundled extract covers April through June 2026, with 10 stores and 201 items.

## Run locally

Use Python 3.12 and [uv](https://docs.astral.sh/uv/). Run these commands from the repository root:

```bash
uv sync
```

Answering questions needs one model backend, described below. Without one, the app shows setup guidance.

For an API backend, first copy the optional settings file and add a key:

```bash
cp .env.example .env
```

Start the server after setting up model access:

```bash
uv run uvicorn freshflow.app:app --port 8077
```

Open [localhost:8077](http://localhost:8077) and choose an example question.

The terminal interface uses the same engine:

```bash
uv run freshflow "Is shrink up or down versus the prior month?"
```

## Weekly report

The weekly report does not call a model. Open the app, choose Weekly report, then pick the team and a Monday–Sunday week. Finance is shrink in dollars for every department, including Grocery, with the prior week beside it and the ten chain-wide items whose shrink increased the most. Store operations is the same layout in units, limited to Produce, Dairy, Meat, Bakery, and Deli, with a section for each store.

A “Show shrink in” toggle switches the view to dollars, units, or both. The team still sets the departments, the stores, and the opening view. In Both, the team’s measure comes first and ranks the item list; the other measure sits beside it. Ask has the same choice as “Shrink in: Auto, Dollars, Units, Both” — Auto lets the question decide, and the others swap or add the paired shrink measure after the planner runs.

The report states the dates actually summed, the departments, and the item ids included. A prior week outside the extract is left blank. It is not filled in as zero.

```bash
uv run freshflow report finance 2026-06-22
uv run freshflow report ops 2026-06-22
```

## Debugging

Each question and report writes a timed trace to `logs/freshflow.log` and keeps the last 40 in memory. In the app, Debug shows that list: the request id, how long planning, the query, the checks, and the written answer each took, and the error text when a step fails. `GET /api/debug` returns the same list.

Run the tests without a model login or API key:

```bash
uv run pytest tests -q
```

The tests cover metric calculations against source-table SQL, selected validation and number checks, backend selection, and environment loading. They don't evaluate live planner or narrator quality.

## Model access

All model calls go through [`llm.py`](src/freshflow/llm.py). It checks backends in this order:

| Backend | Required access | Repository default |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5` |
| `openai` | `OPENAI_API_KEY` | `gpt-5.5` |
| `claude-cli` | `claude` on PATH, signed in | CLI default |
| `codex-cli` | `codex` on PATH, signed in | CLI default |

The API model names are repository settings. Set `FRESHFLOW_MODEL` to use a model your account supports. Set `FRESHFLOW_BACKEND` to choose a backend. Automatic selection checks for keys or executable paths; it doesn't verify credentials or retry another provider after a failure.

Both interfaces load `.env` at startup. Existing environment variables take precedence. API access needs its own key; CLI access uses the installed CLI's login. An answered question makes one model call, to plan the query. The written answer is assembled from the rows. The weekly report does not call a model. A malformed planner JSON reply can trigger a retry.

Each CLI call starts a process in an empty temporary directory. Claude runs with tools disabled. Codex uses a read-only sandbox and a prompt instruction to avoid commands and file reads; its tools aren't disabled.

## Containers

The repository includes a [Dockerfile](Dockerfile) and [devcontainer](.devcontainer/devcontainer.json). Neither installs the model CLIs, so configure an API key for the bundled container setup. VS Code or Cursor can reopen the folder in the devcontainer.

Alternatively, `make docker` builds the image and opens a shell with the repository mounted. Start the server from that shell, or the devcontainer terminal, with:

```bash
uv run uvicorn freshflow.app:app --host 0.0.0.0 --port 8077
```

The Docker image's default command already uses this bind address. Port 8077 is exposed and forwarded by the supplied configurations.

## Files and behavior

[`DESIGN.md`](DESIGN.md) explains the request flow. The main files are:

| File | Purpose |
|---|---|
| [`meridian-extract/`](meridian-extract/) | CSV data and data dictionary |
| [`semantic/meridian.yml`](semantic/meridian.yml) | Measures, dimensions, scope policies, and glossary |
| [`warehouse.py`](src/freshflow/warehouse.py) | Load CSVs into in-memory DuckDB |
| [`spec.py`](src/freshflow/spec.py), [`planner.py`](src/freshflow/planner.py) | Plan structure, validation, and model prompt |
| [`compiler.py`](src/freshflow/compiler.py) | Build and run SQL |
| [`checks.py`](src/freshflow/checks.py) | Sensitivity, caveats, and numeric flags |
| [`engine.py`](src/freshflow/engine.py), [`narrator.py`](src/freshflow/narrator.py) | Build the answer card and prose |

Set `MERIDIAN_EXTRACT` to use a different CSV directory. The semantic file still defines the data window and catalog.

The planner defaults to cost-based shrink and the five fresh departments. Each question stands alone. Item filters match a whole word from the question, so "apples" does not include Apple Juice or Apple Pie. A “why” question still returns a grouped comparison, and the answer says the numbers do not show a cause. Numeric flags check matching values, not whether the prose interprets them correctly. The app has no authentication, saved history, streaming, or caching.
