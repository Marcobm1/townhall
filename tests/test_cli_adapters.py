"""Cross-platform tests (Linux + Windows) for Phase 2 read-only CLI/HTTP adapters & worktree guard."""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from townhall import (
    ClaudeCLIAdapter,
    CodexCLIAdapter,
    GeminiCLIAdapter,
    OpenAIHTTPAdapter,
    OpenCodeCLIAdapter,
    Participant,
    SessionConfig,
    TownHallEngine,
    TurnRequest,
    disposable_worktree,
    opencode_readonly_config,
    sanitize_env,
)
from townhall.cli import main


def _write_fake_cli(tmp_path: Path, name: str, body: str) -> list[str]:
    script = tmp_path / f"{name}.py"
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


def _req(
    pid: str,
    provider: str,
    model: str,
    *,
    phase: str = "independent",
    workspace: str = "",
    effort: str = "",
    timeout: float = 30.0,
) -> TurnRequest:
    return TurnRequest(
        session_id="sess-test",
        phase=phase,
        round_num=1,
        participant=Participant(id=pid, name=pid.title(), provider=provider, model=model, effort=effort),
        system_prompt="System instructions",
        user_prompt="Inspect the repository.",
        workspace=workspace,
        timeout_seconds=timeout,
        read_only=True,
    )


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text("# Demo Repo\n", encoding="utf-8")
    (repo / "sub").mkdir()
    (repo / "sub" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    for args in (
        ["init", "-b", "main"],
        ["config", "user.name", "Test"],
        ["config", "user.email", "test@example.com"],
        ["add", "."],
        ["commit", "-m", "initial"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def test_sanitize_env_strips_secrets_without_mutating_host_env():
    host = {
        "PATH": "/usr/bin",
        "BATON_ACCESS_KEY": "secret-1",
        "TOWNHALL_SECRET": "secret-2",
        "GITHUB_TOKEN": "ghp_xxx",
        "GH_TOKEN": "gho_yyy",
        "ANTHROPIC_API_KEY": "sk-ant-zzz",
        "OPENCODE_PERMISSION": "allow-all",
        "SAFE_VAR": "keep-me",
    }
    clean = sanitize_env(host)
    assert clean == {"PATH": "/usr/bin", "SAFE_VAR": "keep-me"}
    assert "GITHUB_TOKEN" in host  # caller dict untouched


def test_claude_cli_adapter_read_only_flags_json_parsing_and_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "should-not-leak")
    monkeypatch.setenv("BATON_SECRET", "should-not-leak")
    cmd = _write_fake_cli(
        tmp_path,
        "fake_claude",
        """
import json, os, sys
stdin_text = sys.stdin.read()
assert "GITHUB_TOKEN" not in os.environ
assert "BATON_SECRET" not in os.environ
assert "--permission-mode" in sys.argv and "plan" in sys.argv
assert "--allowedTools" in sys.argv and "Read,Glob,Grep" in sys.argv
assert "--dangerously-skip-permissions" not in sys.argv
assert "Inspect the repository." in stdin_text
print(json.dumps({
    "result": "Claude read-only diagnosis",
    "usage": {"input_tokens": 120, "output_tokens": 45},
}))
""",
    )
    adapter = ClaudeCLIAdapter(binary=cmd)
    res = adapter.run_turn(_req("claude", "claude-code", "claude-opus", effort="medium", workspace=str(tmp_path)))
    assert res.ok
    assert res.content == "Claude read-only diagnosis"
    assert res.tokens_in == 120 and res.tokens_out == 45

    slow_cmd = _write_fake_cli(tmp_path, "slow_claude", "import time; time.sleep(5)")
    res_to = ClaudeCLIAdapter(binary=slow_cmd).run_turn(
        _req("claude", "claude-code", "claude-opus", timeout=0.2)
    )
    assert not res_to.ok and "Timed out" in res_to.error


def test_codex_cli_adapter_uses_read_only_sandbox_and_stdin(tmp_path):
    cmd = _write_fake_cli(
        tmp_path,
        "fake_codex",
        """
import sys
prompt = sys.stdin.read()
assert sys.argv[1:4] == ["exec", "-s", "read-only"]
assert "--dangerously-bypass-approvals-and-sandbox" not in sys.argv
assert sys.argv[-1] == "-"
assert "System instructions" in prompt and "Inspect the repository." in prompt
print("Codex read-only review complete")
""",
    )
    res = CodexCLIAdapter(binary=cmd).run_turn(
        _req("codex", "codex", "gpt-5.6-astra", effort="high", workspace=str(tmp_path))
    )
    assert res.ok
    assert res.content == "Codex read-only review complete"


def test_gemini_cli_adapter_parses_stream_json_and_never_bypasses_permissions(tmp_path):
    cmd = _write_fake_cli(
        tmp_path,
        "fake_agy",
        """
import json, sys
assert "-p" in sys.argv
assert "--output-format" in sys.argv and "stream-json" in sys.argv
assert "--dangerously-skip-permissions" not in sys.argv
print(json.dumps({"event": "init", "init": {"model": "gemini-4-neon"}}))
print(json.dumps({"event": "step_update", "text": "reading..."}))
print(json.dumps({
    "event": "result",
    "result": {
        "response": "Gemini stream-json answer",
        "usage": {"input_tokens": 300, "output_tokens": 80},
    },
}))
""",
    )
    res = GeminiCLIAdapter(binary=cmd).run_turn(
        _req("gemini", "gemini", "gemini-4-neon", effort="low", workspace=str(tmp_path))
    )
    assert res.ok
    assert res.content == "Gemini stream-json answer"
    assert res.tokens_in == 300 and res.tokens_out == 80


def test_opencode_cli_adapter_enforces_deny_first_rules_and_strips_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_PERMISSION", "allow-all-bypass")
    cfg = opencode_readonly_config()
    perms = cfg["agents"]["townhall-ro"]["permissions"]
    assert perms[0] == {"action": "*", "resource": "*", "effect": "deny"}
    assert {"action": "read", "resource": "*", "effect": "allow"} in perms
    assert {"action": "execute", "resource": "*", "effect": "deny"} in perms

    cmd = _write_fake_cli(
        tmp_path,
        "fake_opencode",
        """
import json, os, sys
assert "OPENCODE_PERMISSION" not in os.environ
raw_cfg = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
assert "townhall-ro" in raw_cfg["agents"]
assert "--agent" in sys.argv and "townhall-ro" in sys.argv
assert "-m" in sys.argv and "opencode-go/deepseek-v4-pro" in sys.argv
print("OpenCode read-only guest insight")
""",
    )
    res = OpenCodeCLIAdapter(binary=cmd).run_turn(
        _req("guest", "opencode", "opencode-go/deepseek-v4-pro", workspace=str(tmp_path))
    )
    assert res.ok
    assert res.content == "OpenCode read-only guest insight"


def test_openai_http_adapter_talks_to_local_server_and_validates_url():
    with pytest.raises(ValueError):
        OpenAIHTTPAdapter("file:///etc/passwd")

    seen_auth: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen_auth.append(self.headers.get("Authorization", ""))
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            resp = {
                "choices": [{"message": {"content": f"Local reply for {body['model']}"}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            }
            encoded = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _fmt, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = OpenAIHTTPAdapter(f"http://127.0.0.1:{port}", api_key="local-tok")
        res = adapter.run_turn(_req("local", "local", "qwen3.8-local"))
        assert res.ok
        assert res.content == "Local reply for qwen3.8-local"
        assert res.tokens_in == 50 and res.tokens_out == 20
        assert seen_auth == ["Bearer local-tok"]
    finally:
        server.shutdown()
        server.server_close()


def test_disposable_worktree_isolates_and_discards_stray_writes_on_git_repo(tmp_path):
    repo = tmp_path / "my-project"
    _init_git_repo(repo)

    # Even if a misbehaving CLI modifies/deletes/adds files inside its workspace,
    # the original repository must remain 100% untouched after exiting the guard.
    wt_seen = ""
    with disposable_worktree(repo / "sub") as ws:
        assert ws.isolated is True
        wt_seen = ws.path
        wt_path = Path(ws.path)
        assert wt_path != (repo / "sub").resolve()
        assert (wt_path / "app.py").is_file()
        # Simulate stray mutations inside the isolated worktree.
        (wt_path / "app.py").write_text("CORRUPTED\n", encoding="utf-8")
        (wt_path / "evil.txt").write_text("stray file\n", encoding="utf-8")

    assert not Path(wt_seen).exists()
    assert (repo / "sub" / "app.py").read_text(encoding="utf-8") == "print('hello')\n"
    assert not (repo / "sub" / "evil.txt").exists()
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert status == ""

    # Non-git directory passes through safely with isolated=False.
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with disposable_worktree(plain) as ws2:
        assert ws2.isolated is False
        assert Path(ws2.path) == plain.resolve()


def test_end_to_end_council_with_cli_adapters_and_disposable_worktree(tmp_path):
    repo = tmp_path / "site-repo"
    _init_git_repo(repo)
    markers_dir = tmp_path / "seen_workspaces"
    markers_dir.mkdir()

    universal_fake = _write_fake_cli(
        tmp_path,
        "fake_agent",
        f"""
import json, os, sys, time
from pathlib import Path

mode = sys.argv[1]
prompt = sys.stdin.read() if mode in ("claude", "codex") else sys.argv[-1]
# Write one marker file per invocation to avoid concurrent append races on Windows.
out_dir = Path({str(markers_dir)!r})
(out_dir / f"{{mode}}-{{os.getpid()}}-{{time.time_ns()}}.json").write_text(
    json.dumps({{"mode": mode, "cwd": os.getcwd()}}),
    encoding="utf-8",
)

# Simulate a rogue write by gemini inside the worktree — must never reach original repo.
if mode == "gemini":
    Path("rogue_gemini_edit.txt").write_text("oops", encoding="utf-8")

if "vote for ONE complementary guest model" in prompt or "Allowed guest models" in prompt:
    out = json.dumps({{"primary": "opencode-go/qwen4-max", "reason": "great second opinion"}})
elif "Return the closing JSON report." in prompt:
    out = json.dumps({{
        "summary": "All four CLI adapters ran in an isolated worktree.",
        "conclusion": "Everything works cleanly and safely.",
        "agreements": ["Disposable worktree protected the repo"],
        "disagreements": [],
        "action_items": ["Move to Phase 3"],
    }})
else:
    out = f"{{mode}} reviewed README in {{Path('README.md').read_text(encoding='utf-8').strip()}}"

if mode == "claude":
    print(json.dumps({{"result": out, "usage": {{"input_tokens": 10, "output_tokens": 5}}}}))
elif mode == "gemini":
    print(json.dumps({{"event": "result", "result": {{"response": out, "usage": {{"input_tokens": 11, "output_tokens": 6}}}}}}))
else:
    print(out)
""",
    )

    adapters = {
        "claude-code": ClaudeCLIAdapter(binary=[*universal_fake, "claude"]),
        "codex": CodexCLIAdapter(binary=[*universal_fake, "codex"]),
        "gemini": GeminiCLIAdapter(binary=[*universal_fake, "gemini"]),
        "opencode": OpenCodeCLIAdapter(binary=[*universal_fake, "opencode"]),
    }
    participants = [
        Participant(id="gemini", name="Gemini", provider="gemini", model="gemini-4-neon", role="core"),
        Participant(id="codex", name="Codex", provider="codex", model="gpt-5.6-astra", role="core"),
        Participant(id="claude", name="Claude", provider="claude-code", model="claude-opus", role="core"),
    ]
    cfg = SessionConfig(
        topic="Review this repo",
        participants=participants,
        mode="council",
        workspace=str(repo),
        guest_pool=["opencode-go/deepseek-v4-pro", "opencode-go/qwen4-max"],
        debate_rounds=1,
        moderator_id="claude",
    )
    record = TownHallEngine(adapters).run(cfg)

    assert record.status == "completed"
    assert record.guest_selection is not None
    assert record.guest_selection.chosen_model == "opencode-go/qwen4-max"
    assert record.conclusion is not None
    assert "Everything works cleanly" in record.conclusion.conclusion

    # Every subprocess ran inside the isolated worktree, never inside `repo`,
    # and the rogue file written by gemini never touched `repo`.
    cwds = [
        json.loads(p.read_text(encoding="utf-8"))["cwd"]
        for p in sorted(markers_dir.glob("*.json"))
    ]
    assert len(cwds) >= 12 and all(Path(c).resolve() != repo.resolve() for c in cwds)
    assert not (repo / "rogue_gemini_edit.txt").exists()


def test_cli_adapters_command_lists_providers(capsys):
    assert main(["adapters"]) == 0
    out = capsys.readouterr().out
    for name in ("claude-code", "codex", "gemini", "opencode"):
        assert name in out


def test_empty_workspace_uses_isolated_temp_cwd_and_codex_skip_git_check(tmp_path):
    fake_codex = _write_fake_cli(
        tmp_path,
        "fake_codex_empty",
        """
import json, os, sys
sys.stdin.read()
print(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "entries": os.listdir(os.getcwd())}))
""",
    )
    adapter = CodexCLIAdapter(binary=fake_codex)
    req = TurnRequest(
        session_id="s-empty",
        phase="independent",
        round_num=1,
        participant=Participant(id="codex", name="Codex", provider="codex", model="gpt-5.6-astra"),
        system_prompt="sys",
        user_prompt="user",
        workspace="",
    )
    res = adapter.run_turn(req)
    assert res.ok, res.error
    info = json.loads(res.content)
    assert "--skip-git-repo-check" in info["argv"]
    assert Path(info["cwd"]).resolve() != Path.cwd().resolve()
    assert info["entries"] == []


def test_cli_adapter_kills_subprocess_promptly_on_cancel(tmp_path):
    import time

    sleeper = _write_fake_cli(
        tmp_path,
        "slow_cli",
        "import time\ntime.sleep(30)\n",
    )
    cancel = threading.Event()
    threading.Timer(0.25, cancel.set).start()

    adapter = OpenCodeCLIAdapter(binary=sleeper)
    req = TurnRequest(
        session_id="s-cancel",
        phase="independent",
        round_num=1,
        participant=Participant(id="oc", name="OpenCode", provider="opencode", model="m"),
        system_prompt="sys",
        user_prompt="user",
        workspace="",
        timeout_seconds=30.0,
        cancel_event=cancel,
    )
    t0 = time.monotonic()
    res = adapter.run_turn(req)
    elapsed = time.monotonic() - t0
    assert not res.ok
    assert res.error == "Cancelled"
    assert elapsed < 4.0
