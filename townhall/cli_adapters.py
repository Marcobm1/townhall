"""Read-only one-shot CLI & OpenAI-compatible HTTP adapters (standard library only).

Every CLI adapter:
  - Strips host secrets (`BATON_*`, `TOWNHALL_*`, `GITHUB_TOKEN`, `GH_TOKEN`,
    `GITHUB_PAT`, `ANTHROPIC_API_KEY`, `OPENCODE_PERMISSION`) via `sanitize_env()`.
  - Invokes native binaries directly (never through `shell=True` or `.cmd` wrappers
    on Windows when a native `.exe` exists).
  - Enforces strict read-only permissions per CLI (`--permission-mode plan` +
    `--allowedTools Read,Glob,Grep` for Claude Code; `-s read-only` for Codex;
    deny-first `townhall-ro` agent rules for OpenCode; no `--dangerously-skip-permissions`
    for Gemini `agy`, paired with `disposable_worktree` for git repositories).
  - Honors `request.timeout_seconds` and returns errors cleanly in `TurnResult.error`
    instead of crashing the entire Town Hall.
"""

from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Sequence

from .models import TurnRequest, TurnResult

_NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_WRAPPER_EXTS = (".cmd", ".ps1", ".bat")

STRIPPED_ENV_PREFIXES = ("BATON_", "TOWNHALL_")
STRIPPED_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",   # avoid API billing instead of Claude subscription login
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_PAT",
    "OPENCODE_PERMISSION", # prevent overriding deny-first OpenCode rules
})


def sanitize_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a clean copy of the process environment with host secrets removed."""
    source = os.environ if base is None else base
    out: dict[str, str] = {}
    for k, v in source.items():
        ku = k.upper()
        if ku in STRIPPED_ENV_VARS or any(ku.startswith(p) for p in STRIPPED_ENV_PREFIXES):
            continue
        out[k] = v
    return out


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _combined_prompt(request: TurnRequest) -> str:
    parts = [p.strip() for p in (request.system_prompt, request.user_prompt) if p and p.strip()]
    return "\n\n".join(parts)


def _normalize_command_prefix(binary: str | Sequence[str]) -> list[str]:
    if isinstance(binary, str):
        return [binary]
    return [str(part) for part in binary]


# ----------------------------------------------------------- binary discovery
def find_claude() -> str | None:
    explicit = os.environ.get("TOWNHALL_CLAUDE_BIN") or os.environ.get("BATON_CLAUDE_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    pattern = str(
        Path.home()
        / ".vscode"
        / "extensions"
        / "anthropic.claude-code-*"
        / "resources"
        / "native-binary"
        / ("claude.exe" if os.name == "nt" else "claude")
    )
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def _resolve_codex_native(wrapper: str) -> str | None:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        arch, triple = "arm64", "aarch64-pc-windows-msvc"
    else:
        arch, triple = "x64", "x86_64-pc-windows-msvc"
    root = os.path.join(os.path.dirname(wrapper), "node_modules", "@openai")
    candidates = (
        os.path.join(root, f"codex-win32-{arch}", "vendor", triple, "bin", "codex.exe"),
        os.path.join(root, "codex", "node_modules", "@openai", f"codex-win32-{arch}",
                     "vendor", triple, "bin", "codex.exe"),
        os.path.join(root, "codex", "vendor", triple, "bin", "codex.exe"),
        os.path.join(root, f"codex-win32-{arch}", "vendor", triple, "codex", "codex.exe"),
        os.path.join(root, "codex", "bin", "codex.exe"),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def find_codex() -> str | None:
    explicit = os.environ.get("TOWNHALL_CODEX_BIN") or os.environ.get("BATON_CODEX_BIN")
    if explicit:
        return explicit
    if os.name != "nt":
        return shutil.which("codex")
    native = shutil.which("codex.exe")
    if native:
        return native
    wrapper = shutil.which("codex")
    if wrapper and wrapper.lower().endswith(_WRAPPER_EXTS):
        return _resolve_codex_native(wrapper)
    return wrapper


def find_agy() -> str | None:
    explicit = os.environ.get("TOWNHALL_AGY_BIN") or os.environ.get("BATON_AGY_BIN")
    if explicit:
        return explicit
    if os.name != "nt":
        return shutil.which("agy")
    native = shutil.which("agy.exe")
    if native:
        return native
    found = shutil.which("agy")
    if found and found.lower().endswith(_WRAPPER_EXTS):
        candidate = Path(found).with_suffix(".exe")
        return str(candidate) if candidate.is_file() else None
    return found


def find_opencode() -> str | None:
    explicit = os.environ.get("TOWNHALL_OPENCODE_BIN") or os.environ.get("BATON_OPENCODE_BIN")
    if explicit:
        return explicit
    found = shutil.which("opencode")
    if found and found.lower().endswith(_WRAPPER_EXTS):
        exe = Path(found).parent / "node_modules" / "@opencode" / "cli" / "bin" / "opencode.exe"
        return str(exe) if exe.is_file() else None
    return found


def discover_adapters() -> dict[str, str | None]:
    """Report detected local CLI binaries without invoking any model."""
    return {
        "claude-code": find_claude(),
        "codex": find_codex(),
        "gemini": find_agy(),
        "opencode": find_opencode(),
    }


# ----------------------------------------------------------- OpenCode RO rules
def opencode_readonly_config() -> dict:
    """Deny-first OpenCode 2.x agent configuration allowing read/glob/grep only."""

    def _rule(action: str, effect: str, resource: str = "*") -> dict:
        return {"action": action, "resource": resource, "effect": effect}

    rules = (
        [_rule("*", "deny")]
        + [_rule(a, "allow") for a in ("read", "glob", "grep")]
        + [_rule(a, "deny") for a in ("opencode_*", "skill", "subagent", "execute")]
    )
    return {
        "$schema": "https://opencode.ai/config.json",
        "agents": {
            "townhall-ro": {
                "mode": "primary",
                "description": "Read-only participant for Town Hall deliberations",
                "permissions": rules,
            }
        },
    }


# ----------------------------------------------------------- shared runner
def _exec_cli(
    cmd: list[str],
    request: TurnRequest,
    *,
    stdin_text: str | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str] | None, float, str]:
    env = sanitize_env()
    if extra_env:
        env.update(extra_env)
    cwd = request.workspace if request.workspace and os.path.isdir(request.workspace) else None
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            input=stdin_text,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=request.timeout_seconds,
            **_NO_WINDOW,
        )
        return proc, time.monotonic() - t0, ""
    except subprocess.TimeoutExpired:
        return None, time.monotonic() - t0, f"Timed out after {int(request.timeout_seconds)}s"
    except OSError as exc:
        return None, time.monotonic() - t0, str(exc)


def _error_result(request: TurnRequest, error: str, duration: float = 0.0) -> TurnResult:
    return TurnResult(
        participant_id=request.participant.id,
        participant_name=request.participant.name,
        model=request.participant.model,
        phase=request.phase,
        round_num=request.round_num,
        content="",
        duration_seconds=duration,
        error=error,
        created_at=time.time(),
    )


# ----------------------------------------------------------- Claude Code
class ClaudeCLIAdapter:
    """One-shot read-only Claude Code CLI adapter (`claude -p --permission-mode plan`)."""

    def __init__(self, binary: str | Sequence[str] | None = None):
        self._binary = binary

    def _cmd_prefix(self) -> list[str] | None:
        if self._binary is not None:
            return _normalize_command_prefix(self._binary)
        found = find_claude()
        return [found] if found else None

    def run_turn(self, request: TurnRequest) -> TurnResult:
        prefix = self._cmd_prefix()
        if not prefix:
            return _error_result(request, "Claude Code CLI not found (set TOWNHALL_CLAUDE_BIN).")
        cmd = [
            *prefix,
            "-p",
            "--permission-mode", "plan",
            "--output-format", "json",
            "--strict-mcp-config",
            "--setting-sources", "local",
            "--allowedTools", "Read,Glob,Grep",
        ]
        if request.participant.model:
            cmd.extend(["--model", request.participant.model])
        if request.participant.effort:
            cmd.extend(["--effort", request.participant.effort])

        proc, dur, err = _exec_cli(cmd, request, stdin_text=_combined_prompt(request))
        if err or proc is None:
            return _error_result(request, err or "Claude CLI execution failed", dur)
        if proc.returncode != 0:
            detail = strip_ansi(proc.stderr or proc.stdout).strip()[-400:]
            return _error_result(request, f"Claude exited {proc.returncode}: {detail}", dur)

        raw = strip_ansi(proc.stdout).strip()
        text, tok_in, tok_out = _parse_claude_output(raw)
        return TurnResult(
            participant_id=request.participant.id,
            participant_name=request.participant.name,
            model=request.participant.model,
            phase=request.phase,
            round_num=request.round_num,
            content=text,
            duration_seconds=dur,
            tokens_in=tok_in,
            tokens_out=tok_out,
            created_at=time.time(),
        )


def _parse_claude_output(raw: str) -> tuple[str, int, int]:
    try:
        data = json.loads(raw)
    except ValueError:
        return raw, 0, 0
    if isinstance(data, dict):
        result = str(data.get("result") or data.get("content") or "").strip()
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        tok_in = int(usage.get("input_tokens") or 0)
        tok_out = int(usage.get("output_tokens") or 0)
        return result or raw, tok_in, tok_out
    return raw, 0, 0


# ----------------------------------------------------------- Codex
class CodexCLIAdapter:
    """One-shot read-only Codex CLI adapter (`codex exec -s read-only -`)."""

    def __init__(self, binary: str | Sequence[str] | None = None):
        self._binary = binary

    def _cmd_prefix(self) -> list[str] | None:
        if self._binary is not None:
            return _normalize_command_prefix(self._binary)
        found = find_codex()
        return [found] if found else None

    def run_turn(self, request: TurnRequest) -> TurnResult:
        prefix = self._cmd_prefix()
        if not prefix:
            return _error_result(request, "Codex CLI not found (set TOWNHALL_CODEX_BIN).")
        cmd = [*prefix, "exec", "-s", "read-only", "--color", "never"]
        if request.workspace and os.path.isdir(request.workspace):
            cmd.extend(["-C", request.workspace])
        if request.participant.model:
            cmd.extend(["-m", request.participant.model])
        if request.participant.effort:
            cmd.extend(["-c", f"model_reasoning_effort={json.dumps(request.participant.effort)}"])
        cmd.append("-")

        proc, dur, err = _exec_cli(cmd, request, stdin_text=_combined_prompt(request))
        if err or proc is None:
            return _error_result(request, err or "Codex CLI execution failed", dur)
        if proc.returncode != 0:
            detail = strip_ansi(proc.stderr or proc.stdout).strip()[-400:]
            return _error_result(request, f"Codex exited {proc.returncode}: {detail}", dur)

        return TurnResult(
            participant_id=request.participant.id,
            participant_name=request.participant.name,
            model=request.participant.model,
            phase=request.phase,
            round_num=request.round_num,
            content=strip_ansi(proc.stdout).strip(),
            duration_seconds=dur,
            created_at=time.time(),
        )


# ----------------------------------------------------------- Gemini (agy)
class GeminiCLIAdapter:
    """One-shot Gemini Antigravity CLI adapter (`agy -p ... --output-format stream-json`).

    Never passes `--dangerously-skip-permissions`. Pair with `disposable_worktree`
    when inspecting git repositories so any accidental write is discarded.
    """

    def __init__(self, binary: str | Sequence[str] | None = None):
        self._binary = binary

    def _cmd_prefix(self) -> list[str] | None:
        if self._binary is not None:
            return _normalize_command_prefix(self._binary)
        found = find_agy()
        return [found] if found else None

    def run_turn(self, request: TurnRequest) -> TurnResult:
        prefix = self._cmd_prefix()
        if not prefix:
            return _error_result(request, "Gemini Antigravity CLI (agy) not found (set TOWNHALL_AGY_BIN).")
        cmd = [*prefix, "-p", _combined_prompt(request), "--output-format", "stream-json"]
        if request.participant.model:
            cmd.extend(["--model", request.participant.model])
        if request.participant.effort:
            cmd.extend(["--effort", request.participant.effort])

        proc, dur, err = _exec_cli(cmd, request, stdin_text=None)
        if err or proc is None:
            return _error_result(request, err or "Gemini CLI execution failed", dur)
        if proc.returncode != 0:
            detail = strip_ansi(proc.stderr or proc.stdout).strip()[-400:]
            return _error_result(request, f"Gemini (agy) exited {proc.returncode}: {detail}", dur)

        raw = strip_ansi(proc.stdout).strip()
        text, tok_in, tok_out = _parse_agy_stream_json(raw)
        return TurnResult(
            participant_id=request.participant.id,
            participant_name=request.participant.name,
            model=request.participant.model,
            phase=request.phase,
            round_num=request.round_num,
            content=text,
            duration_seconds=dur,
            tokens_in=tok_in,
            tokens_out=tok_out,
            created_at=time.time(),
        )


def _parse_agy_stream_json(output: str) -> tuple[str, int, int]:
    """Extract `result.response` and token usage from agy's NDJSON stream."""
    last_result: dict = {}
    for line in output.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            ev = json.loads(s)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("event") == "result" and isinstance(ev.get("result"), dict):
            last_result = ev["result"]
    if not last_result:
        return output, 0, 0
    response = str(last_result.get("response") or "").strip()
    usage = last_result.get("usage") if isinstance(last_result.get("usage"), dict) else {}
    tok_in = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    tok_out = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return response or output, tok_in, tok_out


# ----------------------------------------------------------- OpenCode
class OpenCodeCLIAdapter:
    """One-shot read-only OpenCode CLI adapter using deny-first `townhall-ro` rules."""

    def __init__(self, binary: str | Sequence[str] | None = None):
        self._binary = binary

    def _cmd_prefix(self) -> list[str] | None:
        if self._binary is not None:
            return _normalize_command_prefix(self._binary)
        found = find_opencode()
        return [found] if found else None

    def run_turn(self, request: TurnRequest) -> TurnResult:
        prefix = self._cmd_prefix()
        if not prefix:
            return _error_result(request, "OpenCode CLI not found (set TOWNHALL_OPENCODE_BIN).")
        cmd = [
            *prefix,
            "run",
            "--standalone",
            "--auto",
            "--agent", "townhall-ro",
            "-m", request.participant.model,
            "--archived",
            "--title", f"Town Hall {request.session_id}",
            _combined_prompt(request),
        ]
        extra_env = {"OPENCODE_CONFIG_CONTENT": json.dumps(opencode_readonly_config())}
        proc, dur, err = _exec_cli(cmd, request, stdin_text=None, extra_env=extra_env)
        if err or proc is None:
            return _error_result(request, err or "OpenCode CLI execution failed", dur)
        if proc.returncode != 0:
            detail = strip_ansi(proc.stderr or proc.stdout).strip()[-400:]
            return _error_result(request, f"OpenCode exited {proc.returncode}: {detail}", dur)

        return TurnResult(
            participant_id=request.participant.id,
            participant_name=request.participant.name,
            model=request.participant.model,
            phase=request.phase,
            round_num=request.round_num,
            content=strip_ansi(proc.stdout).strip(),
            duration_seconds=dur,
            created_at=time.time(),
        )


# ----------------------------------------------------------- OpenAI-compatible HTTP
class OpenAIHTTPAdapter:
    """Standard-library adapter for OpenAI-compatible `/v1/chat/completions` servers.

    Ideal for local `llama-server`, Ollama, LM Studio, or any self-hosted endpoint.
    """

    def __init__(self, base_url: str, *, api_key: str = ""):
        url = (base_url or "").strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        self.base_url = url
        self._api_key = (api_key or "").strip()

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def run_turn(self, request: TurnRequest) -> TurnResult:
        t0 = time.monotonic()
        payload = json.dumps({
            "model": request.participant.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": False,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=request.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:240]
            except OSError:
                pass
            return _error_result(request, f"HTTP {exc.code}: {body}", time.monotonic() - t0)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _error_result(request, f"HTTP error: {exc}", time.monotonic() - t0)

        dur = time.monotonic() - t0
        try:
            data = json.loads(raw)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = str(msg.get("content") or "").strip()
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            tok_in = int(usage.get("prompt_tokens") or 0)
            tok_out = int(usage.get("completion_tokens") or 0)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return _error_result(request, f"Malformed JSON response: {exc}", dur)

        return TurnResult(
            participant_id=request.participant.id,
            participant_name=request.participant.name,
            model=request.participant.model,
            phase=request.phase,
            round_num=request.round_num,
            content=content,
            duration_seconds=dur,
            tokens_in=tok_in,
            tokens_out=tok_out,
            created_at=time.time(),
        )


def build_default_adapters(
    *,
    local_base_url: str = "",
    local_api_key: str = "",
) -> dict[str, object]:
    """Build the standard provider->adapter registry for real Town Hall runs."""
    registry: dict[str, object] = {
        "claude-code": ClaudeCLIAdapter(),
        "codex": CodexCLIAdapter(),
        "gemini": GeminiCLIAdapter(),
        "opencode": OpenCodeCLIAdapter(),
    }
    if local_base_url:
        registry["local"] = OpenAIHTTPAdapter(local_base_url, api_key=local_api_key)
    return registry
