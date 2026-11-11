"""DuckDB warehouse. Loads the three CSVs into one daily fact table.

The fact table merges sales and shipments at store-item-day grain so every
measure in the semantic layer can be written against a single alias. Days with
no movement are absent in the source, so the merge is a full outer join on the
grain keys.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACT = REPO_ROOT / "meridian-extract"

_BUILD = """
CREATE OR REPLACE TABLE items AS
SELECT * FROM read_csv_auto('{d}/items.csv');

CREATE OR REPLACE TABLE sales_daily AS
SELECT * FROM read_csv_auto('{d}/sales_daily.csv');

CREATE OR REPLACE TABLE shipments AS
SELECT * FROM read_csv_auto('{d}/shipments.csv');

CREATE OR REPLACE TABLE fact AS
SELECT
    COALESCE(s.date, h.date)           AS date,
    COALESCE(s.store_id, h.store_id)   AS store_id,
    COALESCE(s.banner, h.banner)       AS banner,
    COALESCE(s.region, h.region)       AS region,
    COALESCE(s.item_id, h.item_id)     AS item_id,
    COALESCE(s.units_sold, 0)          AS units_sold,
    COALESCE(s.net_sales, 0)           AS net_sales,
    COALESCE(h.cases_received, 0)      AS cases_received
FROM sales_daily s
FULL OUTER JOIN shipments h
  ON s.date = h.date AND s.store_id = h.store_id AND s.item_id = h.item_id;
"""


def extract_dir(override: str | os.PathLike | None = None) -> Path:
    return Path(override or os.environ.get("MERIDIAN_EXTRACT", DEFAULT_EXTRACT)).resolve()


def connect(override: str | os.PathLike | None = None) -> duckdb.DuckDBPyConnection:
    """Build an in-memory warehouse over the extract and return the connection."""
    d = extract_dir(override)
    for name in ("items.csv", "sales_daily.csv", "shipments.csv"):
        if not (d / name).exists():
            raise FileNotFoundError(f"missing {name} in {d}")
    con = duckdb.connect()
    con.execute(_BUILD.format(d=d.as_posix()))
    return con
