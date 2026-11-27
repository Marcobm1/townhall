"""Command-line interface for Town Hall (`townhall demo|list|show|export`)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .adapter import DemoAdapter
from .engine import TownHallEngine
from .models import Participant, SessionConfig, SessionEvent
from .store import TownHallStore, render_markdown

DEFAULT_GUEST_POOL = [
    "opencode-go/deepseek-v4-pro",
    "opencode-go/qwen4-max",
    "opencode-go/kimi-k3.1-pro",
    "opencode-go/glm-5.3",
]


def default_db_path() -> Path:
    env_path = os.environ.get("TOWNHALL_DB", "").strip()
    if env_path:
        return Path(env_path)
    return Path.home() / ".townhall" / "townhall.sqlite3"


def default_core_participants() -> list[Participant]:
    return [
        Participant(id="gemini", name="Gemini", provider="gemini", model="gemini-4-neon", role="core"),
        Participant(id="codex", name="Codex", provider="codex", model="gpt-5.6-astra", role="core"),
        Participant(id="claude", name="Claude", provider="claude-code", model="claude-opus", role="core"),
    ]


def _print_event(ev: SessionEvent) -> None:
    if ev.type == "phase_started":
        print(f"-> Phase: {ev.payload.get('phase')} (round {ev.payload.get('round_num')})")
    elif ev.type == "guest_selected":
        print(
            f"-> 4th guest chosen by {ev.payload.get('method')}: "
            f"{ev.payload.get('chosen_model')}"
        )
    elif ev.type == "turn_completed":
        status = "ok" if ev.payload.get("ok") else f"error: {ev.payload.get('error')}"
        print(f"   [{ev.payload.get('participant_name')}] {status}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="townhall", description="Multi-agent AI Town Hall CLI.")
    p.add_argument("--db", default="", help="Path to SQLite database (default: ~/.townhall/townhall.sqlite3)")
    sub = p.add_subparsers(dest="cmd", required=True)

    demo = sub.add_parser("demo", help="Run an offline simulated 4-agent Town Hall session.")
    demo.add_argument("topic", help="Question or review prompt for the Town Hall.")
    demo.add_argument("--mode", choices=["council", "open_floor"], default="council")
    demo.add_argument("--rounds", type=int, default=1, help="Cross-critique rounds in council mode (0-3).")
    demo.add_argument("--max-turns", type=int, default=6, help="Turn cap in open_floor mode.")
    demo.add_argument("--max-seconds", type=float, default=120.0, help="Hard session duration budget.")
    demo.add_argument("--moderator", default="claude", help="Participant ID that writes the final synthesis.")
    demo.add_argument("--lang", default="es", choices=["es", "en"])

    ls = sub.add_parser("list", help="List saved Town Hall sessions.")
    ls.add_argument("--limit", type=int, default=20)

    show = sub.add_parser("show", help="Print a saved Town Hall report to stdout.")
    show.add_argument("session_id", help="Session ID to inspect.")

    exp = sub.add_parser("export", help="Export a saved Town Hall session as Markdown.")
    exp.add_argument("session_id", help="Session ID to export.")
    exp.add_argument("-o", "--output", required=True, help="Output Markdown file path.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db) if args.db else default_db_path()
    store = TownHallStore(db_path)

    if args.cmd == "demo":
        config = SessionConfig(
            topic=args.topic,
            participants=default_core_participants(),
            mode=args.mode,
            guest_pool=list(DEFAULT_GUEST_POOL),
            debate_rounds=args.rounds,
            max_turns=args.max_turns,
            max_duration_seconds=args.max_seconds,
            moderator_id=args.moderator,
            language=args.lang,
        )
        engine = TownHallEngine(DemoAdapter(), on_event=_print_event)
        record = engine.run(config)
        store.save(record)
        print()
        print(render_markdown(record))
        print(f"Saved session `{record.id}` in {db_path}")
        return 0

    if args.cmd == "list":
        rows = store.list_sessions(limit=args.limit)
        if not rows:
            print("No Town Hall sessions recorded yet.")
            return 0
        for r in rows:
            guest = f" + {r['guest_model']}" if r["guest_model"] else ""
            print(f"{r['id']}  [{r['mode']}/{r['status']}]  {r['topic'][:72]}{guest}")
        return 0

    if args.cmd == "show":
        record = store.get(args.session_id)
        if record is None:
            print(f"Session not found: {args.session_id}", file=sys.stderr)
            return 1
        print(render_markdown(record))
        return 0

    if args.cmd == "export":
        record = store.get(args.session_id)
        if record is None:
            print(f"Session not found: {args.session_id}", file=sys.stderr)
            return 1
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(record), encoding="utf-8")
        print(f"Exported {args.session_id} -> {out}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
