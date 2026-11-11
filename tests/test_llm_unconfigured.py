import os

import pytest

from freshflow import llm
from freshflow.engine import Engine
from freshflow.env import load_env

MODEL_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FRESHFLOW_BACKEND", "FRESHFLOW_MODEL")


@pytest.fixture
def clean_env(monkeypatch):
    """No keys set and, unless a test says otherwise, no model CLI on PATH."""
    for var in MODEL_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda cmd: None)
    return monkeypatch


def on_path(monkeypatch, *commands: str) -> None:
    monkeypatch.setattr(llm.shutil, "which", lambda cmd: f"/bin/{cmd}" if cmd in commands else None)


def test_unconfigured_model_answers_with_the_setup_note(clean_env):
    card = Engine().ask("Is shrink up or down versus the prior month?")
    assert card.answerable is False
    assert card.answer == llm.NOT_CONFIGURED
    assert card.backend == "not configured"


@pytest.mark.parametrize(
    ("env", "clis", "expected"),
    [
        ({"ANTHROPIC_API_KEY": "a"}, (), "anthropic:claude-sonnet-5"),
        ({"OPENAI_API_KEY": "o"}, (), "openai:gpt-5.5"),
        ({"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"}, (), "anthropic:claude-sonnet-5"),
        ({"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "FRESHFLOW_BACKEND": "openai"}, (),
         "openai:gpt-5.5"),
        ({"OPENAI_API_KEY": "o", "FRESHFLOW_MODEL": "gpt-5.4"}, (), "openai:gpt-5.4"),
        # No key: fall back to a subscription CLI, Claude Code first.
        ({}, ("claude", "codex"), "claude-cli:default"),
        ({}, ("codex",), "codex-cli:default"),
        # A key beats a CLI unless the CLI is chosen.
        ({"OPENAI_API_KEY": "o"}, ("claude",), "openai:gpt-5.5"),
        ({"OPENAI_API_KEY": "o", "FRESHFLOW_BACKEND": "codex-cli"}, ("codex",), "codex-cli:default"),
        ({"FRESHFLOW_BACKEND": "claude-cli", "FRESHFLOW_MODEL": "opus"}, ("claude",),
         "claude-cli:opus"),
        # Chosen but not available, or not a backend at all.
        ({"ANTHROPIC_API_KEY": "a", "FRESHFLOW_BACKEND": "openai"}, (), "not configured"),
        ({"FRESHFLOW_BACKEND": "codex-cli"}, ("claude",), "not configured"),
        ({"ANTHROPIC_API_KEY": "a", "FRESHFLOW_BACKEND": "gemini"}, (), "misconfigured"),
    ],
)
def test_backend_follows_what_is_available(clean_env, env, clis, expected):
    for k, v in env.items():
        clean_env.setenv(k, v)
    on_path(clean_env, *clis)
    assert llm.backend_name() == expected


def test_cli_failure_becomes_a_readable_error(clean_env):
    clean_env.setenv("FRESHFLOW_BACKEND", "claude-cli")
    on_path(clean_env, "claude")

    def fail(cmd, **kwargs):
        return llm.subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in")

    clean_env.setattr(llm.subprocess, "run", fail)
    with pytest.raises(llm.LLMError, match="not logged in"):
        llm.complete("system", "user")


def test_env_file_fills_unset_vars_only(clean_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nOPENAI_API_KEY=from-file\nANTHROPIC_API_KEY=\nFRESHFLOW_MODEL='m-file'\n"
    )
    clean_env.setenv("FRESHFLOW_MODEL", "from-shell")
    load_env(env_file)
    assert os.environ["OPENAI_API_KEY"] == "from-file"
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["FRESHFLOW_MODEL"] == "from-shell"
