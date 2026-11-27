"""Consensus voting for selecting the 4th guest model (majority -> Borda -> tie-break)."""

from __future__ import annotations

import json
import re
from collections import Counter

from .models import ConsensusSelection, GuestVote

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(text: str) -> dict | None:
    """Extract the first valid JSON object from raw model output (handles code fences)."""
    raw = (text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    for m in _FENCE_RE.finditer(raw):
        candidates.insert(0, m.group(1).strip())
    m_brace = _BRACE_RE.search(raw)
    if m_brace:
        candidates.append(m_brace.group(0).strip())
    for chunk in candidates:
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _match_candidate(choice: str, allowed_by_lower: dict[str, str]) -> str:
    cleaned = (choice or "").strip().strip("`\"'")
    if not cleaned:
        return ""
    return allowed_by_lower.get(cleaned.casefold(), "")


def parse_guest_vote(
    voter_id: str,
    voter_name: str,
    raw_content: str,
    allowed_models: list[str],
) -> GuestVote:
    """Parse and strictly validate a voter's guest-model ballot against `allowed_models`."""
    allowed_by_lower = {m.casefold(): m for m in allowed_models if m.strip()}
    data = extract_json_object(raw_content)
    if not data:
        return GuestVote(
            voter_id=voter_id,
            voter_name=voter_name,
            primary="",
            secondary="",
            reason="",
            raw_content=raw_content,
            valid=False,
        )
    primary = _match_candidate(str(data.get("primary") or data.get("model") or ""), allowed_by_lower)
    secondary = _match_candidate(str(data.get("secondary") or data.get("backup") or ""), allowed_by_lower)
    if secondary == primary:
        secondary = ""
    reason = str(data.get("reason") or "").strip()[:300]
    return GuestVote(
        voter_id=voter_id,
        voter_name=voter_name,
        primary=primary,
        secondary=secondary,
        reason=reason,
        raw_content=raw_content,
        valid=bool(primary),
    )


def resolve_consensus(
    votes: list[GuestVote],
    allowed_models: list[str],
) -> ConsensusSelection:
    """Pick one guest model deterministically:

    1. Unanimous / strict majority of valid primary votes (> N/2).
    2. Borda count across all valid ballots (primary = 2 pts, distinct secondary = 1 pt).
    3. Tie-break by most primary votes, then lexicographical model ID.
    4. Fallback to the first model in `allowed_models` if no valid votes exist.
    """
    pool = [m.strip() for m in allowed_models if m.strip()]
    if not pool:
        raise ValueError("allowed_models must not be empty")

    valid_votes = [v for v in votes if v.valid and v.primary in pool]
    if not valid_votes:
        return ConsensusSelection(
            chosen_model=pool[0],
            method="fallback",
            votes=list(votes),
            scores={pool[0]: 0},
        )

    primary_counts = Counter(v.primary for v in valid_votes)
    borda_scores: Counter[str] = Counter()
    for v in valid_votes:
        borda_scores[v.primary] += 2
        if v.secondary and v.secondary in pool and v.secondary != v.primary:
            borda_scores[v.secondary] += 1

    top_model, top_primaries = primary_counts.most_common(1)[0]
    if top_primaries == len(valid_votes) and len(valid_votes) > 1:
        return ConsensusSelection(
            chosen_model=top_model,
            method="unanimous",
            votes=list(votes),
            scores=dict(borda_scores),
        )
    if top_primaries * 2 > len(valid_votes):
        return ConsensusSelection(
            chosen_model=top_model,
            method="majority",
            votes=list(votes),
            scores=dict(borda_scores),
        )

    ranked = sorted(
        borda_scores.keys(),
        key=lambda m: (-borda_scores[m], -primary_counts[m], m.casefold()),
    )
    winner = ranked[0]
    has_unique_borda_winner = (
        len(ranked) == 1 or borda_scores[ranked[0]] > borda_scores[ranked[1]]
    )
    method = "borda" if has_unique_borda_winner else "tie_break"
    return ConsensusSelection(
        chosen_model=winner,
        method=method,
        votes=list(votes),
        scores=dict(borda_scores),
    )


def rank_candidates(
    selection: ConsensusSelection,
    allowed_models: list[str],
) -> list[str]:
    """Return candidate guest models in strict consensus fallback order.

    Starts with `selection.chosen_model`, then remaining scored candidates sorted
    by Borda score -> primary votes -> model ID, then remaining pool entries.
    """
    primary_counts = Counter(v.primary for v in selection.votes if v.valid and v.primary)
    ranked: list[str] = []
    seen: set[str] = set()

    def _add(model: str) -> None:
        m = (model or "").strip()
        if not m:
            return
        key = m.casefold()
        if key not in seen:
            seen.add(key)
            ranked.append(m)

    _add(selection.chosen_model)
    scored = sorted(
        selection.scores.keys(),
        key=lambda m: (-int(selection.scores.get(m, 0)), -primary_counts.get(m, 0), m.casefold()),
    )
    for m in scored:
        _add(m)
    for m in allowed_models:
        _add(m)
    return ranked
