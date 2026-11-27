"""Tests for Phase 0 consensus voting (unanimous, majority, Borda tie-break, strict allowlist)."""

from __future__ import annotations

import pytest

from townhall.consensus import parse_guest_vote, resolve_consensus

POOL = [
    "opencode-go/deepseek-v4-pro",
    "opencode-go/qwen4-max",
    "opencode-go/kimi-k3.1-pro",
    "opencode-go/glm-5.3",
]


def test_unanimous_and_majority_selection():
    ballots_u = [
        parse_guest_vote("gemini", "Gemini", '{"primary": "opencode-go/qwen4-max", "reason": "best"}', POOL),
        parse_guest_vote("codex", "Codex", '```json\n{"primary": "opencode-go/qwen4-max", "secondary": "opencode-go/glm-5.3"}\n```', POOL),
        parse_guest_vote("claude", "Claude", 'Here is my pick: {"primary": "OPENCODE-GO/QWEN4-MAX"}', POOL),
    ]
    sel_u = resolve_consensus(ballots_u, POOL)
    assert sel_u.chosen_model == "opencode-go/qwen4-max"
    assert sel_u.method == "unanimous"
    assert all(v.valid for v in sel_u.votes)

    ballots_m = [
        parse_guest_vote("gemini", "Gemini", '{"primary": "opencode-go/deepseek-v4-pro", "secondary": "opencode-go/qwen4-max"}', POOL),
        parse_guest_vote("codex", "Codex", '{"primary": "opencode-go/deepseek-v4-pro", "secondary": "opencode-go/kimi-k3.1-pro"}', POOL),
        parse_guest_vote("claude", "Claude", '{"primary": "opencode-go/qwen4-max", "secondary": "opencode-go/deepseek-v4-pro"}', POOL),
    ]
    sel_m = resolve_consensus(ballots_m, POOL)
    assert sel_m.chosen_model == "opencode-go/deepseek-v4-pro"
    assert sel_m.method == "majority"


def test_three_way_split_resolved_by_borda_secondary_votes():
    # 1-1-1 primary tie; qwen4-max receives two secondary votes (2 + 1 + 1 = 4 pts).
    ballots = [
        parse_guest_vote("gemini", "Gemini", '{"primary": "opencode-go/deepseek-v4-pro", "secondary": "opencode-go/qwen4-max"}', POOL),
        parse_guest_vote("codex", "Codex", '{"primary": "opencode-go/kimi-k3.1-pro", "secondary": "opencode-go/qwen4-max"}', POOL),
        parse_guest_vote("claude", "Claude", '{"primary": "opencode-go/qwen4-max", "secondary": "opencode-go/glm-5.3"}', POOL),
    ]
    sel = resolve_consensus(ballots, POOL)
    assert sel.chosen_model == "opencode-go/qwen4-max"
    assert sel.method == "borda"
    assert sel.scores["opencode-go/qwen4-max"] == 4


def test_exact_tie_breaks_deterministically_and_hallucinated_models_are_rejected():
    # Exact tie (2 pts each, no secondaries) breaks alphabetically.
    ballots_tie = [
        parse_guest_vote("gemini", "Gemini", '{"primary": "opencode-go/qwen4-max"}', POOL),
        parse_guest_vote("codex", "Codex", '{"primary": "opencode-go/glm-5.3"}', POOL),
    ]
    sel_tie = resolve_consensus(ballots_tie, POOL)
    assert sel_tie.chosen_model == "opencode-go/glm-5.3"
    assert sel_tie.method == "tie_break"

    # Hallucinated or non-JSON ballots are never allowed outside `POOL`.
    bad_ballots = [
        parse_guest_vote("gemini", "Gemini", '{"primary": "evil/unlisted-model"}', POOL),
        parse_guest_vote("codex", "Codex", "not json at all", POOL),
    ]
    assert all(not b.valid for b in bad_ballots)
    sel_fb = resolve_consensus(bad_ballots, POOL)
    assert sel_fb.chosen_model == POOL[0]
    assert sel_fb.method == "fallback"

    with pytest.raises(ValueError):
        resolve_consensus([], [])
