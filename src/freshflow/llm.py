"""The one place the prototype talks to a model.

`complete` takes a system prompt and a user prompt and returns text. The
callers parse JSON out of that text, so any chat API that returns a string
works here. Four are wired:

  * anthropic  — the Anthropic API. Needs ANTHROPIC_API_KEY.
  * openai     — the OpenAI API. Needs OPENAI_API_KEY.
  * claude-cli — Claude Code (`claude -p`), on your Claude subscription login.
  * codex-cli  — Codex (`codex exec`), on your ChatGPT login.

FRESHFLOW_BACKEND picks one. If it is unset, the first available backend in that
order is used: an API whose key is set, then a CLI found on PATH.
FRESHFLOW_MODEL overrides the model. Settings are read at call time, so a `.env`
loaded at startup takes effect.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

BACKENDS = ("anthropic", "openai", "claude-cli", "codex-cli")
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5", "openai": "gpt-5.5"}
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
CLI_COMMANDS = {"claude-cli": "claude", "codex-cli": "codex"}
CLI_TIMEOUT_S = 180

NOT_CONFIGURED = (
    "No model is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env, or "
    "install and sign in to Claude Code (`claude`) or Codex (`codex`). "
    "See \"Model access\" in README.md."
)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class LLMError(RuntimeError):
    pass


def _available(name: str) -> bool:
    if name in KEY_VARS:
        return bool(os.environ.get(KEY_VARS[name]))
    return shutil.which(CLI_COMMANDS[name]) is not None


def backend() -> str | None:
    """The backend to call, or None when none is available."""
    chosen = os.environ.get("FRESHFLOW_BACKEND", "").strip().lower()
    if chosen:
        if chosen not in BACKENDS:
            raise LLMError(f"FRESHFLOW_BACKEND={chosen!r} is not one of: {', '.join(BACKENDS)}")
        return chosen if _available(chosen) else None
    return next((b for b in BACKENDS if _available(b)), None)


def model() -> str | None:
    """The model to request. None lets a CLI use its own default."""
    return os.environ.get("FRESHFLOW_MODEL") or DEFAULT_MODELS.get(backend() or "anthropic")


def configured() -> bool:
    return backend() is not None


def _via_anthropic(system: str, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic()
    try:
        msg = client.messages.create(
            model=model(),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIError as exc:
        raise LLMError(f"The Anthropic API call failed: {exc}") from exc
    return "".join(b.text for b in msg.content if b.type == "text")


def _via_openai(system: str, user: str, max_tokens: int) -> str:
    import openai

    client = openai.OpenAI()
    try:
        resp = client.responses.create(
            model=model(),
            instructions=system,
            input=user,
            max_output_tokens=max_tokens,
        )
    except openai.APIError as exc:
        raise LLMError(f"The OpenAI API call failed: {exc}") from exc
    return resp.output_text


def _run_cli(name: str, cmd: list[str], prompt: str, cwd: str) -> subprocess.CompletedProcess:
    """Run a model CLI with the prompt on stdin; raise LLMError on any failure."""
    try:
        result = subprocess.run(
            cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=CLI_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMError(f"{name} did not answer within {CLI_TIMEOUT_S}s") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip()[-500:]
        raise LLMError(
            f"{name} failed (exit {result.returncode}). If you are not signed in, run "
            f"`{CLI_COMMANDS[name]}` once and sign in. Output: {tail}"
        )
    return result


# Both CLIs run in an empty temporary directory with tools off or read-only, so
# they answer from the prompt alone and can't read this repo or its instructions.

def _via_claude_cli(system: str, user: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            "claude", "-p",
            "--system-prompt", system,
            "--tools", "",
            "--output-format", "text",
            "--no-session-persistence",
            "--setting-sources", "",
            "--strict-mcp-config",
        ]
        if model():
            cmd += ["--model", model()]
        return _run_cli("claude-cli", cmd, user, tmp).stdout


def _via_codex_cli(system: str, user: str) -> str:
    # `codex exec` has no separate system prompt, so the system text leads.
    prompt = f"{system}\n\nDo not run commands or read files.\n\n{user}"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "reply.txt"
        cmd = [
            "codex", "exec",
            "--skip-git-repo-check", "--ephemeral",
            "--sandbox", "read-only",
            "--color", "never",
            "--output-last-message", str(out),
        ]
        if model():
            cmd += ["--model", model()]
        cmd.append("-")
        _run_cli("codex-cli", cmd, prompt, tmp)
        if not out.exists():
            raise LLMError("codex-cli finished without writing a reply")
        return out.read_text()


def complete(system: str, user: str, max_tokens: int = 2000) -> str:
    """max_tokens applies to the API backends; the CLIs set their own limits."""
    name = backend()
    if name is None:
        raise LLMError(NOT_CONFIGURED)
    if name == "openai":
        return _via_openai(system, user, max_tokens)
    if name == "claude-cli":
        return _via_claude_cli(system, user)
    if name == "codex-cli":
        return _via_codex_cli(system, user)
    return _via_anthropic(system, user, max_tokens)


def complete_json(system: str, user: str, max_tokens: int = 2000) -> dict:
    """Ask for JSON and parse it. One retry with the parse error fed back."""
    text = complete(system, user, max_tokens)
    try:
        return _parse_json(text)
    except ValueError as exc:
        retry = f"{user}\n\nYour last reply could not be parsed: {exc}\nReply with JSON only."
        try:
            return _parse_json(complete(system, retry, max_tokens))
        except ValueError as retry_exc:
            raise LLMError(f"The model did not return valid JSON after a retry: {retry_exc}") from retry_exc


def _parse_json(text: str) -> dict:
    block = _JSON_BLOCK.search(text)
    candidate = block.group(1) if block else text
    candidate = candidate.strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in reply")
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc


def backend_name() -> str:
    try:
        name = backend()
    except LLMError:
        return "misconfigured"  # complete() raises the same error with the detail
    return f"{name}:{model() or 'default'}" if name else "not configured"
