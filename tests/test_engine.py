"""Tests for TownHallEngine (`council` + `open_floor`, blind Round 1, budgets, cancellation, faults)."""

from __future__ import annotations

import json
import threading

from townhall import (
    CallableAdapter,
    DemoAdapter,
    Participant,
    SessionConfig,
    SessionEvent,
    TownHallEngine,
    TurnRequest,
    TurnResult,
)


def _core() -> list[Participant]:
    return [
        Participant(id="gemini", name="Gemini", provider="gemini", model="gemini-4-neon", role="core"),
        Participant(id="codex", name="Codex", provider="codex", model="gpt-5.6-astra", role="core"),
        Participant(id="claude", name="Claude", provider="claude-code", model="claude-opus", role="core"),
    ]


POOL = [
    "opencode-go/deepseek-v4-pro",
    "opencode-go/qwen4-max",
    "opencode-go/kimi-k3.1-pro",
]


def test_council_runs_blind_round_1_cross_critique_and_synthesis():
    seen_requests: list[TurnRequest] = []
    events: list[SessionEvent] = []

    def handler(req: TurnRequest) -> str:
        seen_requests.append(req)
        assert req.read_only is True
        if req.phase == "vote_guest":
            return json.dumps({"primary": "opencode-go/qwen4-max", "reason": "strong review"})
        if req.phase == "independent":
            return f"SECRET_MARKER_{req.participant.id}: unique analysis"
        if req.phase == "debate":
            return f"Critique by {req.participant.id} in round {req.round_num}"
        if req.phase == "synthesis":
            return json.dumps({
                "summary": "Four models converged cleanly.",
                "conclusion": "Ship Phase 2 CLI adapters next.",
                "agreements": ["Read-only worktree guard"],
                "disagreements": ["One model wanted 2 rounds by default"],
                "action_items": ["Implement Claude/Codex/Gemini/OpenCode adapters"],
            })
        raise AssertionError(f"unexpected phase {req.phase}")

    engine = TownHallEngine(CallableAdapter(handler), on_event=events.append)
    cfg = SessionConfig(
        topic="Review the website status and list what is missing",
        participants=_core(),
        mode="council",
        guest_pool=POOL,
        debate_rounds=1,
        moderator_id="claude",
    )
    record = engine.run(cfg, session_id="sess-council-1")

    assert record.status == "completed"
    assert record.guest_selection is not None
    assert record.guest_selection.chosen_model == "opencode-go/qwen4-max"
    assert record.guest_selection.method == "unanimous"
    assert [p.id for p in record.participants] == ["gemini", "codex", "claude", "guest"]

    # Round 1 (independent) must be strictly blind: no peer markers in any Round 1 prompt.
    indep_reqs = [r for r in seen_requests if r.phase == "independent"]
    assert len(indep_reqs) == 4
    for r in indep_reqs:
        assert "SECRET_MARKER_" not in r.user_prompt

    # Round 2 (debate) must show all 3 peers' Round 1 markers plus own previous position.
    debate_reqs = [r for r in seen_requests if r.phase == "debate"]
    assert len(debate_reqs) == 4
    for r in debate_reqs:
        for pid in ("gemini", "codex", "claude", "guest"):
            assert f"SECRET_MARKER_{pid}" in r.user_prompt

    # Synthesis ran with the chosen moderator (claude) and parsed structured fields.
    assert record.conclusion is not None
    assert record.conclusion.moderator_id == "claude"
    assert record.conclusion.conclusion == "Ship Phase 2 CLI adapters next."
    assert record.conclusion.agreements == ["Read-only worktree guard"]
    assert record.conclusion.disagreements == ["One model wanted 2 rounds by default"]
    assert record.conclusion.action_items == ["Implement Claude/Codex/Gemini/OpenCode adapters"]

    event_types = [e.type for e in events]
    assert event_types[0] == "session_started"
    assert "guest_selected" in event_types
    assert event_types[-1] == "session_completed"


def test_open_floor_honors_time_budget_and_still_synthesizes():
    # Simulated clock: advances by 35 seconds each turn; 80s budget stops after 2 turns -> synthesis.
    now = [0.0]

    def clock() -> float:
        return now[0]

    def handler(req: TurnRequest) -> str:
        if req.phase == "open_floor":
            now[0] += 35.0
            return f"Thought from {req.participant.name} at t={now[0]}"
        if req.phase == "synthesis":
            return json.dumps({
                "summary": "Time-boxed discussion summarized cleanly.",
                "conclusion": "Keep weekly reviews under 10 minutes.",
                "agreements": ["Budget cutoff worked"],
                "disagreements": [],
                "action_items": ["Add scheduler in Phase 3"],
            })
        return "ok"

    events: list[SessionEvent] = []
    engine = TownHallEngine(CallableAdapter(handler), on_event=events.append, clock=clock)
    cfg = SessionConfig(
        topic="Weekly open discussion on project health",
        participants=_core()[:2],
        mode="open_floor",
        max_turns=10,
        max_duration_seconds=80.0,
    )
    record = engine.run(cfg)

    floor_turns = [t for t in record.turns if t.phase == "open_floor"]
    assert len(floor_turns) == 3  # t=0 (runs -> 35), t=35 (runs -> 70), t=70 (runs -> 105 >= 80 -> stops)
    assert record.budget_reached is True
    assert record.status == "completed"
    assert record.conclusion is not None
    assert "Keep weekly reviews" in record.conclusion.conclusion
    assert any(e.type == "budget_reached" for e in events)


def test_cancellation_and_single_participant_failure_resilience():
    # 1. Pre-cancelled event aborts immediately.
    cancel = threading.Event()
    cancel.set()
    engine = TownHallEngine(DemoAdapter())
    cfg = SessionConfig(topic="Cancel me", participants=_core(), guest_pool=POOL)
    cancelled = engine.run(cfg, cancel_event=cancel)
    assert cancelled.status == "cancelled"
    assert cancelled.turns == []

    # 2. One participant crashing does not abort the deliberation.
    def flaky(req: TurnRequest) -> str:
        if req.participant.id == "codex":
            raise RuntimeError("CLI timed out")
        if req.phase == "synthesis":
            return "Plain text conclusion without JSON."
        return f"Valid response from {req.participant.name}"

    resilient_engine = TownHallEngine(CallableAdapter(flaky))
    cfg2 = SessionConfig(topic="Survive flaky CLI", participants=_core(), debate_rounds=1, moderator_id="claude")
    rec = resilient_engine.run(cfg2)
    assert rec.status == "completed"
    assert any(t.participant_id == "codex" and t.error == "CLI timed out" for t in rec.turns)
    assert rec.conclusion is not None
    assert "Plain text conclusion" in rec.conclusion.conclusion


def test_guest_pool_deduplicates_core_models_and_falls_back_on_region_error():
    from townhall import prompts

    seen_vote_prompts: list[str] = []
    debate_models: list[str] = []
    events: list[str] = []

    def handler(req: TurnRequest) -> str:
        if req.phase == "vote_guest":
            seen_vote_prompts.append(req.user_prompt)
            return json.dumps({
                "primary": "opencode-go/deepseek-v4-pro",
                "secondary": "opencode-go/gemini-4-argon",
                "reason": "independent second opinion",
            })
        if req.participant.model == "opencode-go/deepseek-v4-pro":
            raise RuntimeError("This OpenCode Go model is hosted outside Europe and the United States.")
        if req.phase == "debate":
            debate_models.append(req.participant.model)
        if req.phase == "synthesis":
            return json.dumps({
                "summary": "Fallback guest completed the council.",
                "conclusion": "Ship the fix.",
                "agreements": ["Resilience matters"],
                "disagreements": [],
                "action_items": ["Add fallback"],
            })
        return f"Position from {req.participant.name} ({req.participant.model})"

    participants = [
        Participant(id="gemini", name="Gemini", provider="gemini", model="gemini-4-neon", role="core"),
        Participant(id="opencode_1", name="OpenCode #1", provider="opencode", model="opencode-go/claude-haiku-5-5", role="core"),
    ]
    cfg = SessionConfig(
        topic="Test guest dedup and fallback",
        participants=participants,
        guest_pool=[
            "opencode-go/claude-haiku-5-5",  # already in core -> must be excluded
            "opencode-go/deepseek-v4-pro",   # fails with region error -> triggers fallback
            "opencode-go/gemini-4-argon",    # #2 Borda candidate -> succeeds
        ],
        debate_rounds=1,
        moderator_id="gemini",
    )
    engine = TownHallEngine(CallableAdapter(handler), on_event=lambda ev: events.append(ev.type))
    rec = engine.run(cfg)

    assert rec.status == "completed"
    # Core model never appeared in the allowed guest candidate list.
    assert seen_vote_prompts and all("- opencode-go/claude-haiku-5-5" not in p for p in seen_vote_prompts)
    # Fallback replaced DeepSeek with Gemini 4 Argon before Debate Round 1.
    assert "guest_fallback" in events
    assert rec.guest_selection is not None
    assert rec.guest_selection.chosen_model == "opencode-go/gemini-4-argon"
    assert rec.guest_selection.method.endswith("_fallback")
    assert "opencode-go/gemini-4-argon" in debate_models
    assert "opencode-go/deepseek-v4-pro" not in debate_models

    # Prompt budgeting keeps debate & synthesis prompts safely under Windows 32K CLI limit.
    huge = "x" * 12000
    big_turns = [
        TurnResult("p1", "P1", "m1", "independent", 1, huge),
        TurnResult("p2", "P2", "m2", "independent", 1, huge),
        TurnResult("p3", "P3", "m3", "independent", 1, huge),
        TurnResult("p4", "P4", "m4", "debate", 1, huge),
    ]
    s_sys, s_usr = prompts.synthesis_prompts("Big topic", participants[0], big_turns, rec.guest_selection, False, "es")
    assert len(s_sys) + len(s_usr) < 26000
    d_sys, d_usr = prompts.debate_prompts("Big topic", participants[0], 1, huge, big_turns[:3], "es")
    assert len(d_sys) + len(d_usr) < 26000
