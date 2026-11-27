"""Tests for Phase 3: recurring TownHallScheduler, allowed hours, standalone web UI, SSE & CSRF."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from townhall import (
    DemoAdapter,
    Participant,
    ScheduleSpec,
    TownHallScheduler,
    TownHallStore,
    create_server,
    in_allowed_hours,
    is_schedule_due,
)
from townhall.cli import main


def _core() -> list[Participant]:
    return [
        Participant(id="gemini", name="Gemini", provider="gemini", model="gemini-4-neon", role="core"),
        Participant(id="codex", name="Codex", provider="codex", model="gpt-5.6-astra", role="core"),
        Participant(id="claude", name="Claude", provider="claude-code", model="claude-opus", role="core"),
    ]


def test_allowed_hours_windows_and_midnight_wrap():
    assert in_allowed_hours("", dt.datetime(2026, 11, 27, 3, 0)) is True
    assert in_allowed_hours("09:00-21:00", dt.datetime(2026, 11, 27, 9, 0)) is True
    assert in_allowed_hours("09:00-21:00", dt.datetime(2026, 11, 27, 20, 59)) is True
    assert in_allowed_hours("09:00-21:00", dt.datetime(2026, 11, 27, 21, 0)) is False
    assert in_allowed_hours("09:00-21:00", dt.datetime(2026, 11, 27, 8, 59)) is False
    # Overnight window (22:00 to 06:00).
    assert in_allowed_hours("22:00-06:00", dt.datetime(2026, 11, 27, 23, 30)) is True
    assert in_allowed_hours("22:00-06:00", dt.datetime(2026, 11, 27, 2, 15)) is True
    assert in_allowed_hours("22:00-06:00", dt.datetime(2026, 11, 27, 14, 0)) is False
    # Malformed strings fail closed.
    assert in_allowed_hours("99:00-10:00", dt.datetime(2026, 11, 27, 10, 0)) is False
    assert in_allowed_hours("not-a-window", dt.datetime(2026, 11, 27, 10, 0)) is False


def test_scheduler_tick_respects_interval_window_and_fires_callback(tmp_path):
    store = TownHallStore(tmp_path / "townhall.sqlite3")
    spec = ScheduleSpec(
        id="weekly-huo",
        name="Weekly Huo Review",
        topic="Review Huo status and next priorities",
        participants=_core(),
        guest_pool=["opencode-go/deepseek-v4-pro", "opencode-go/qwen4-max"],
        interval_hours=168.0,
        allowed_hours="09:00-21:00",
    )
    store.save_schedule(spec)

    finished_calls: list[tuple[str, str]] = []
    scheduler = TownHallScheduler(
        store,
        DemoAdapter(),
        on_finished=lambda s, r: finished_calls.append((s.id, r.id)),
    )

    # 1. Outside allowed hours (03:00) -> does NOT run.
    early_dt = dt.datetime(2026, 11, 27, 3, 0)
    assert is_schedule_due(spec, now_ts=early_dt.timestamp(), now_dt=early_dt) is False
    assert scheduler.tick(now_ts=early_dt.timestamp(), now_dt=early_dt) == []

    # 2. Inside allowed hours (11:00) -> runs once and updates last_run_at / last_session_id.
    midday_dt = dt.datetime(2026, 11, 27, 11, 0)
    ran = scheduler.tick(now_ts=midday_dt.timestamp(), now_dt=midday_dt)
    assert len(ran) == 1
    assert ran[0].status == "completed"
    assert finished_calls == [("weekly-huo", ran[0].id)]

    updated = store.get_schedule("weekly-huo")
    assert updated is not None and updated.last_session_id == ran[0].id

    # 3. One hour later -> interval (168h) not elapsed -> skipped.
    later_dt = midday_dt + dt.timedelta(hours=1)
    assert scheduler.tick(now_ts=later_dt.timestamp(), now_dt=later_dt) == []

    # 4. 7 days later -> due again unless paused.
    next_week = midday_dt + dt.timedelta(hours=168)
    store.set_schedule_enabled("weekly-huo", False)
    assert scheduler.tick(now_ts=next_week.timestamp(), now_dt=next_week) == []
    store.set_schedule_enabled("weekly-huo", True)
    ran2 = scheduler.tick(now_ts=next_week.timestamp(), now_dt=next_week)
    assert len(ran2) == 1 and ran2[0].id != ran[0].id


def _http_get(url: str) -> tuple[int, str, dict[str, str]]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8"), dict(resp.headers)


def _http_post_form(url: str, data: dict[str, str]) -> tuple[int, str, str]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8"), resp.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), exc.headers.get("Location", "")


def test_standalone_web_server_csrf_sessions_xss_escape_and_schedules(tmp_path):
    store = TownHallStore(tmp_path / "web.sqlite3")
    server, runtime = create_server(store, host="127.0.0.1", port=0, demo=True)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        st, body, headers = _http_get(f"{base}/healthz")
        assert st == 200 and json.loads(body)["ok"] is True
        assert headers.get("X-Content-Type-Options") == "nosniff"

        st_home, home_html, _ = _http_get(f"{base}/")
        assert st_home == 200 and "Nueva sesión de Town Hall" in home_html

        # POST without valid CSRF must be rejected with 403.
        st_bad, _, _ = _http_post_form(f"{base}/sessions", {"csrf": "wrong", "topic": "Hi"})
        assert st_bad == 403

        # Start a session with an XSS payload in topic; verify escaping and completion.
        xss_topic = "Check <script>alert(1)</script> security"
        st_create, _, loc = _http_post_form(
            f"{base}/sessions",
            {
                "csrf": runtime.csrf_token,
                "topic": xss_topic,
                "mode": "council",
                "rounds": "1",
                "max_seconds": "60",
                "moderator": "claude",
            },
        )
        assert st_create == 303 and loc.startswith("/sessions/")
        sid = loc.split("/")[-1]

        # Wait for background DemoAdapter run to finish.
        deadline = time.time() + 10.0
        rec = None
        while time.time() < deadline:
            rec = store.get(sid)
            if rec and rec.status == "completed":
                break
            time.sleep(0.05)
        assert rec is not None and rec.status == "completed"

        st_detail, detail_html, _ = _http_get(f"{base}/sessions/{sid}")
        assert st_detail == 200
        assert "<script>alert(1)</script>" not in detail_html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in detail_html
        assert "Conclusión" in detail_html
        assert "Resumen ejecutivo" in detail_html
        assert "Elección del 4º modelo invitado" in detail_html
        assert "Conversación completa" in detail_html

        st_md, md_text, _ = _http_get(f"{base}/sessions/{sid}.md")
        assert st_md == 200 and "## Conclusion" in md_text

        st_api, api_text, _ = _http_get(f"{base}/api/sessions/{sid}")
        assert st_api == 200 and json.loads(api_text)["status"] == "completed"

        # SSE endpoint returns a clean completion event for a finished session.
        st_sse, sse_body, _ = _http_get(f"{base}/api/sessions/{sid}/events")
        assert st_sse == 200 and "session_completed" in sse_body

        # Schedule lifecycle via HTTP: create -> toggle -> run now -> delete.
        st_sc, _, _ = _http_post_form(
            f"{base}/schedules",
            {
                "csrf": runtime.csrf_token,
                "name": "Semanal",
                "topic": "Revisión semanal",
                "interval_hours": "168",
                "allowed_hours": "09:00-21:00",
            },
        )
        assert st_sc == 303
        scheds = store.list_schedules()
        assert len(scheds) == 1
        sc_id = scheds[0].id

        assert _http_post_form(f"{base}/schedules/{sc_id}/toggle", {"csrf": runtime.csrf_token})[0] == 303
        assert store.get_schedule(sc_id).enabled is False

        st_run, _, run_loc = _http_post_form(f"{base}/schedules/{sc_id}/run", {"csrf": runtime.csrf_token})
        assert st_run == 303 and run_loc.startswith("/sessions/")

        assert _http_post_form(f"{base}/schedules/{sc_id}/delete", {"csrf": runtime.csrf_token})[0] == 303
        assert store.list_schedules() == []
    finally:
        runtime.stop()
        server.shutdown()
        server.server_close()


def test_cli_schedule_subcommands(tmp_path, capsys):
    db = str(tmp_path / "cli_sched.sqlite3")
    assert main(["--db", db, "schedule", "add", "Weekly", "Check repo", "--hours", "168"]) == 0
    out = capsys.readouterr().out
    assert "Created schedule" in out
    sc_id = out.split()[2].rstrip(":")

    assert main(["--db", db, "schedule", "list"]) == 0
    assert "Weekly" in capsys.readouterr().out

    assert main(["--db", db, "schedule", "tick", "--demo"]) == 0
    assert "Ran 1 due schedule(s)." in capsys.readouterr().out

    assert main(["--db", db, "schedule", "toggle", sc_id]) == 0
    assert "paused" in capsys.readouterr().out

    assert main(["--db", db, "schedule", "delete", sc_id]) == 0
    assert "Deleted schedule" in capsys.readouterr().out
