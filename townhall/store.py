"""SQLite WAL persistence and Markdown report export for Town Hall sessions."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import (
    Conclusion,
    ConsensusSelection,
    GuestVote,
    Participant,
    ScheduleSpec,
    SessionRecord,
    TurnResult,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    workspace TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'es',
    participants_json TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL DEFAULT 0,
    guest_model TEXT NOT NULL DEFAULT '',
    guest_method TEXT NOT NULL DEFAULT '',
    guest_scores_json TEXT NOT NULL DEFAULT '{}',
    moderator_id TEXT NOT NULL DEFAULT '',
    moderator_name TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    conclusion TEXT NOT NULL DEFAULT '',
    agreements_json TEXT NOT NULL DEFAULT '[]',
    disagreements_json TEXT NOT NULL DEFAULT '[]',
    action_items_json TEXT NOT NULL DEFAULT '[]',
    budget_reached INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    voter_id TEXT NOT NULL,
    voter_name TEXT NOT NULL,
    primary_choice TEXT NOT NULL,
    secondary_choice TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    valid INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    phase TEXT NOT NULL,
    round_num INTEGER NOT NULL,
    participant_id TEXT NOT NULL,
    participant_name TEXT NOT NULL,
    model TEXT NOT NULL,
    content TEXT NOT NULL,
    duration_seconds REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    topic TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'council',
    workspace TEXT NOT NULL DEFAULT '',
    participants_json TEXT NOT NULL,
    guest_pool_json TEXT NOT NULL DEFAULT '[]',
    guest_provider TEXT NOT NULL DEFAULT 'opencode',
    debate_rounds INTEGER NOT NULL DEFAULT 1,
    max_turns INTEGER NOT NULL DEFAULT 8,
    max_duration_seconds REAL NOT NULL DEFAULT 600.0,
    turn_timeout_seconds REAL NOT NULL DEFAULT 180.0,
    moderator_id TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'es',
    interval_hours REAL NOT NULL DEFAULT 168.0,
    allowed_hours TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at REAL NOT NULL DEFAULT 0,
    last_session_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id);
CREATE INDEX IF NOT EXISTS idx_votes_session ON votes(session_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
"""


class TownHallStore:
    """Durable SQLite WAL storage for sessions, votes, turns, and conclusions."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def save(self, record: SessionRecord) -> None:
        guest = record.guest_selection
        concl = record.conclusion
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, topic, mode, status, workspace, language, participants_json,
                    started_at, finished_at,
                    guest_model, guest_method, guest_scores_json,
                    moderator_id, moderator_name, summary, conclusion,
                    agreements_json, disagreements_json, action_items_json,
                    budget_reached, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    participants_json=excluded.participants_json,
                    finished_at=excluded.finished_at,
                    guest_model=excluded.guest_model,
                    guest_method=excluded.guest_method,
                    guest_scores_json=excluded.guest_scores_json,
                    moderator_id=excluded.moderator_id,
                    moderator_name=excluded.moderator_name,
                    summary=excluded.summary,
                    conclusion=excluded.conclusion,
                    agreements_json=excluded.agreements_json,
                    disagreements_json=excluded.disagreements_json,
                    action_items_json=excluded.action_items_json,
                    budget_reached=excluded.budget_reached,
                    error=excluded.error
                """,
                (
                    record.id,
                    record.topic,
                    record.mode,
                    record.status,
                    record.workspace,
                    record.language,
                    json.dumps([p.to_dict() for p in record.participants], ensure_ascii=False),
                    record.started_at,
                    record.finished_at,
                    guest.chosen_model if guest else "",
                    guest.method if guest else "",
                    json.dumps(guest.scores, ensure_ascii=False) if guest else "{}",
                    concl.moderator_id if concl else "",
                    concl.moderator_name if concl else "",
                    concl.summary if concl else "",
                    concl.conclusion if concl else "",
                    json.dumps(concl.agreements, ensure_ascii=False) if concl else "[]",
                    json.dumps(concl.disagreements, ensure_ascii=False) if concl else "[]",
                    json.dumps(concl.action_items, ensure_ascii=False) if concl else "[]",
                    1 if record.budget_reached else 0,
                    record.error,
                ),
            )
            conn.execute("DELETE FROM votes WHERE session_id = ?", (record.id,))
            if guest:
                conn.executemany(
                    """
                    INSERT INTO votes (
                        session_id, voter_id, voter_name, primary_choice, secondary_choice, reason, valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            record.id,
                            v.voter_id,
                            v.voter_name,
                            v.primary,
                            v.secondary,
                            v.reason,
                            1 if v.valid else 0,
                        )
                        for v in guest.votes
                    ],
                )
            conn.execute("DELETE FROM turns WHERE session_id = ?", (record.id,))
            conn.executemany(
                """
                INSERT INTO turns (
                    session_id, phase, round_num, participant_id, participant_name,
                    model, content, duration_seconds, error, tokens_in, tokens_out, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.id,
                        t.phase,
                        t.round_num,
                        t.participant_id,
                        t.participant_name,
                        t.model,
                        t.content,
                        t.duration_seconds,
                        t.error,
                        t.tokens_in,
                        t.tokens_out,
                        t.created_at,
                    )
                    for t in record.turns
                ],
            )

    def get(self, session_id: str) -> SessionRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            vote_rows = conn.execute(
                "SELECT * FROM votes WHERE session_id = ? ORDER BY id ASC", (session_id,)
            ).fetchall()
            turn_rows = conn.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY id ASC", (session_id,)
            ).fetchall()

        participants = [Participant.from_dict(p) for p in json.loads(row["participants_json"])]
        guest_selection = None
        if row["guest_model"]:
            guest_selection = ConsensusSelection(
                chosen_model=row["guest_model"],
                method=row["guest_method"],
                votes=[
                    GuestVote(
                        voter_id=vr["voter_id"],
                        voter_name=vr["voter_name"],
                        primary=vr["primary_choice"],
                        secondary=vr["secondary_choice"],
                        reason=vr["reason"],
                        valid=bool(vr["valid"]),
                    )
                    for vr in vote_rows
                ],
                scores=json.loads(row["guest_scores_json"] or "{}"),
            )
        conclusion = None
        if row["summary"] or row["conclusion"]:
            conclusion = Conclusion(
                moderator_id=row["moderator_id"],
                moderator_name=row["moderator_name"],
                summary=row["summary"],
                conclusion=row["conclusion"],
                agreements=json.loads(row["agreements_json"] or "[]"),
                disagreements=json.loads(row["disagreements_json"] or "[]"),
                action_items=json.loads(row["action_items_json"] or "[]"),
            )
        turns = [
            TurnResult(
                participant_id=tr["participant_id"],
                participant_name=tr["participant_name"],
                model=tr["model"],
                phase=tr["phase"],
                round_num=tr["round_num"],
                content=tr["content"],
                duration_seconds=tr["duration_seconds"],
                error=tr["error"],
                tokens_in=tr["tokens_in"],
                tokens_out=tr["tokens_out"],
                created_at=tr["created_at"],
            )
            for tr in turn_rows
        ]
        return SessionRecord(
            id=row["id"],
            topic=row["topic"],
            mode=row["mode"],
            status=row["status"],
            workspace=row["workspace"],
            language=row["language"],
            participants=participants,
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            guest_selection=guest_selection,
            turns=turns,
            conclusion=conclusion,
            budget_reached=bool(row["budget_reached"]),
            error=row["error"],
        )

    def list_sessions(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, topic, mode, status, guest_model, summary,
                       started_at, finished_at, budget_reached
                FROM sessions
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ schedules
    def save_schedule(self, spec: ScheduleSpec) -> None:
        now = spec.created_at or time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO schedules (
                    id, name, topic, mode, workspace, participants_json,
                    guest_pool_json, guest_provider, debate_rounds, max_turns,
                    max_duration_seconds, turn_timeout_seconds, moderator_id,
                    language, interval_hours, allowed_hours, enabled,
                    last_run_at, last_session_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    topic=excluded.topic,
                    mode=excluded.mode,
                    workspace=excluded.workspace,
                    participants_json=excluded.participants_json,
                    guest_pool_json=excluded.guest_pool_json,
                    guest_provider=excluded.guest_provider,
                    debate_rounds=excluded.debate_rounds,
                    max_turns=excluded.max_turns,
                    max_duration_seconds=excluded.max_duration_seconds,
                    turn_timeout_seconds=excluded.turn_timeout_seconds,
                    moderator_id=excluded.moderator_id,
                    language=excluded.language,
                    interval_hours=excluded.interval_hours,
                    allowed_hours=excluded.allowed_hours,
                    enabled=excluded.enabled
                """,
                (
                    spec.id,
                    spec.name,
                    spec.topic,
                    spec.mode,
                    spec.workspace,
                    json.dumps([p.to_dict() for p in spec.participants], ensure_ascii=False),
                    json.dumps(list(spec.guest_pool), ensure_ascii=False),
                    spec.guest_provider,
                    spec.debate_rounds,
                    spec.max_turns,
                    spec.max_duration_seconds,
                    spec.turn_timeout_seconds,
                    spec.moderator_id,
                    spec.language,
                    spec.interval_hours,
                    spec.allowed_hours,
                    1 if spec.enabled else 0,
                    spec.last_run_at,
                    spec.last_session_id,
                    now,
                ),
            )

    def _row_to_schedule(self, row: sqlite3.Row) -> ScheduleSpec:
        return ScheduleSpec(
            id=row["id"],
            name=row["name"],
            topic=row["topic"],
            participants=[Participant.from_dict(p) for p in json.loads(row["participants_json"])],
            mode=row["mode"],
            workspace=row["workspace"],
            guest_pool=json.loads(row["guest_pool_json"] or "[]"),
            guest_provider=row["guest_provider"],
            debate_rounds=int(row["debate_rounds"]),
            max_turns=int(row["max_turns"]),
            max_duration_seconds=float(row["max_duration_seconds"]),
            turn_timeout_seconds=float(row["turn_timeout_seconds"]),
            moderator_id=row["moderator_id"],
            language=row["language"],
            interval_hours=float(row["interval_hours"]),
            allowed_hours=row["allowed_hours"],
            enabled=bool(row["enabled"]),
            last_run_at=float(row["last_run_at"]),
            last_session_id=row["last_session_id"],
            created_at=float(row["created_at"]),
        )

    def get_schedule(self, schedule_id: str) -> ScheduleSpec | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        return self._row_to_schedule(row) if row is not None else None

    def list_schedules(self) -> list[ScheduleSpec]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM schedules ORDER BY created_at DESC, id ASC").fetchall()
        return [self._row_to_schedule(r) for r in rows]

    def set_schedule_enabled(self, schedule_id: str, enabled: bool) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE schedules SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, schedule_id),
            )
            return cur.rowcount > 0

    def mark_schedule_run(self, schedule_id: str, run_at: float, session_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE schedules SET last_run_at = ?, last_session_id = ? WHERE id = ?",
                (float(run_at), session_id, schedule_id),
            )
            return cur.rowcount > 0

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            return cur.rowcount > 0


def render_markdown(record: SessionRecord) -> str:
    """Render a complete, human-readable Markdown report (Conclusion -> Summary -> Transcript)."""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(record.started_at)) if record.started_at else ""
    lines = [
        f"# Town Hall: {record.topic}",
        "",
        f"- **ID:** `{record.id}`",
        f"- **Mode:** `{record.mode}` · **Status:** `{record.status}`"
        + (" · *(time budget reached)*" if record.budget_reached else ""),
        f"- **Started:** {when}",
        "- **Participants:** " + ", ".join(f"{p.name} (`{p.model}`)" for p in record.participants),
    ]
    if record.workspace:
        lines.append(f"- **Workspace:** `{record.workspace}`")

    if record.conclusion:
        c = record.conclusion
        lines += ["", "## Conclusion", "", c.conclusion, ""]
        lines += ["## Executive Summary", "", c.summary, ""]
        if c.agreements:
            lines += ["### Agreements", ""] + [f"- {a}" for a in c.agreements] + [""]
        if c.disagreements:
            lines += ["### Open Disagreements", ""] + [f"- {d}" for d in c.disagreements] + [""]
        if c.action_items:
            lines += ["### Prioritized Action Plan", ""] + [
                f"{i}. {item}" for i, item in enumerate(c.action_items, 1)
            ] + [""]

    if record.guest_selection:
        g = record.guest_selection
        lines += [
            "## 4th Guest Model Election",
            "",
            f"- **Selected:** `{g.chosen_model}` (method: `{g.method}`)",
        ]
        for v in g.votes:
            sec = f" / backup `{v.secondary}`" if v.secondary else ""
            why = f" — {v.reason}" if v.reason else ""
            lines.append(f"- **{v.voter_name}:** `{v.primary or 'invalid'}`{sec}{why}")
        lines.append("")

    lines += ["## Full Transcript", ""]
    for t in record.turns:
        if t.phase == "vote_guest":
            continue
        if t.phase == "synthesis" and record.conclusion is not None:
            continue
        lines.append(f"### [{t.phase} · round {t.round_num}] {t.participant_name} (`{t.model}`)")
        lines.append("")
        if t.error:
            lines.append(f"> Error: {t.error}")
        else:
            lines.append(t.content.strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
