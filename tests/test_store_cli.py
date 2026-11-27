"""Tests for SQLite WAL TownHallStore, Markdown report rendering, and CLI subcommands."""

from __future__ import annotations

from townhall.cli import main
from townhall.store import TownHallStore, render_markdown


def test_cli_demo_list_show_export_and_sqlite_roundtrip(tmp_path, capsys):
    db_path = tmp_path / "townhall.sqlite3"
    out_md = tmp_path / "report.md"

    rc = main(["--db", str(db_path), "demo", "Review Huo homepage and missing pieces", "--rounds", "1"])
    assert rc == 0
    demo_out = capsys.readouterr().out
    assert "4th guest chosen by majority: opencode-go/deepseek-v4-pro" in demo_out
    assert "## Conclusion" in demo_out
    assert "## Executive Summary" in demo_out
    assert "## Full Transcript" in demo_out

    store = TownHallStore(db_path)
    sessions = store.list_sessions()
    assert len(sessions) == 1
    sid = sessions[0]["id"]
    assert sessions[0]["status"] == "completed"
    assert sessions[0]["guest_model"] == "opencode-go/deepseek-v4-pro"

    loaded = store.get(sid)
    assert loaded is not None
    assert loaded.topic == "Review Huo homepage and missing pieces"
    assert loaded.guest_selection is not None
    assert len(loaded.guest_selection.votes) == 3
    assert len(loaded.participants) == 4
    assert loaded.conclusion is not None and len(loaded.conclusion.action_items) == 3

    md = render_markdown(loaded)
    assert "## Conclusion" in md
    assert "## Executive Summary" in md
    assert "## 4th Guest Model Election" in md
    assert "## Full Transcript" in md

    assert main(["--db", str(db_path), "list"]) == 0
    assert sid in capsys.readouterr().out

    assert main(["--db", str(db_path), "show", sid]) == 0
    assert "Review Huo homepage" in capsys.readouterr().out

    assert main(["--db", str(db_path), "export", sid, "-o", str(out_md)]) == 0
    capsys.readouterr()
    assert out_md.is_file()
    assert "## Conclusion" in out_md.read_text(encoding="utf-8")

    # Unknown session ID exits cleanly with code 1.
    assert main(["--db", str(db_path), "show", "missing-id"]) == 1
