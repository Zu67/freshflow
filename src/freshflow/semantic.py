"""Loads Meridian's business definitions and renders them for the planner."""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

SEMANTIC_PATH = Path(__file__).resolve().parents[2] / "semantic" / "meridian.yml"


class SemanticModel:
    def __init__(self, raw: dict):
        self.raw = raw
        self.dataset = raw["dataset"]
        self.policies = raw["policies"]
        self.measures: dict[str, dict] = raw["measures"]
        self.dimensions: dict[str, dict] = raw["dimensions"]
        self.synonyms: dict[str, str] = raw.get("synonyms", {})

    # -- window ---------------------------------------------------------
    @property
    def window_start(self) -> date:
        return date.fromisoformat(self.dataset["window_start"])

    @property
    def window_end(self) -> date:
        return date.fromisoformat(self.dataset["window_end"])

    @property
    def fresh_departments(self) -> list[str]:
        return list(self.policies["fresh_departments"])

    @property
    def center_store_departments(self) -> list[str]:
        return list(self.policies["center_store_departments"])

    # -- lookups --------------------------------------------------------
    def measure(self, name: str) -> dict:
        if name not in self.measures:
            raise KeyError(f"unknown measure '{name}'")
        return self.measures[name]

    def dimension(self, name: str) -> dict:
        if name not in self.dimensions:
            raise KeyError(f"unknown dimension '{name}'")
        return self.dimensions[name]

    def paired_measure(self, name: str) -> str | None:
        """The same metric expressed on the other defensible basis."""
        return self.measures.get(name, {}).get("pair")

    def describe(self, name: str) -> str:
        m = self.measures.get(name)
        return f"{m['label']} = {m['formula']}" if m else name

    # -- prompt rendering ------------------------------------------------
    def catalog(self) -> str:
        """The definition catalog handed to the planner."""
        lines = [
            f"DATASET: {self.dataset['name']}",
            f"Data window: {self.dataset['window_start']} to {self.dataset['window_end']}"
            f" ({self.dataset['stores']} stores). No data exists outside this window.",
            f"Note: {self.dataset['note'].strip()}",
            "",
            "MEASURES (use these names exactly):",
        ]
        for name, m in self.measures.items():
            lines.append(f"  {name} [{m['unit']}] — {m['label']}: {m['formula']}")
        lines += ["", "DIMENSIONS (use these names exactly):"]
        for name, d in self.dimensions.items():
            vals = f" values: {d['values']}" if "values" in d else ""
            lines.append(f"  {name} — {d['label']}.{vals}")
        lines += [
            "",
            "SCOPE POLICY:",
            f"  fresh = {self.fresh_departments}",
            f"  all   = fresh plus {self.center_store_departments}",
            f"  default scope is '{self.policies['default_scope']}'. {self.policies['scope_rationale'].strip()}",
            f"  default shrink basis is '{self.policies['default_shrink_basis']}'."
            f" {self.policies['basis_rationale'].strip()}",
            "",
            "GLOSSARY:",
        ]
        for k, v in self.synonyms.items():
            lines.append(f"  {k} -> {v}")
        return "\n".join(lines)


@lru_cache(maxsize=1)
def load(path: str | Path = SEMANTIC_PATH) -> SemanticModel:
    return SemanticModel(yaml.safe_load(Path(path).read_text()))
