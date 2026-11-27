"""Prompt builders for each Town Hall deliberation phase."""

from __future__ import annotations

from .models import ConsensusSelection, Participant, TurnResult

# Keep combined prompts safely below Windows CreateProcess's 32,767-char CLI arg
# limit (used by `agy -p <prompt>` and `opencode run ... <prompt>`).
MAX_COMBINED_PROMPT_CHARS = 22000

_LANG_NOTE = {
    "es": "Respond in Spanish (clear, concise, technical).",
    "en": "Respond in English (clear, concise, technical).",
}


def _lang_instruction(lang: str) -> str:
    return _LANG_NOTE.get((lang or "en").lower()[:2], _LANG_NOTE["en"])


def _clip_text(text: str, limit: int) -> str:
    cleaned = (text or "").strip()
    if limit <= 0 or len(cleaned) <= limit:
        return cleaned
    marker = "\n… [truncated for prompt budget]"
    cut = max(80, limit - len(marker))
    return cleaned[:cut].rstrip() + marker


def _budget_turns(
    turns: list[TurnResult],
    *,
    budget_chars: int,
    formatter,
) -> str:
    """Format turns within `budget_chars`, keeping later turns fuller than older ones."""
    ok_turns = [t for t in turns if t.ok]
    if not ok_turns:
        return ""
    raw_blocks = [formatter(t, t.content.strip()) for t in ok_turns]
    if sum(len(b) for b in raw_blocks) + 2 * len(raw_blocks) <= budget_chars:
        return "\n\n".join(raw_blocks)

    # Allocate higher weight to the final round (e.g. debate > independent) so the
    # synthesis or next turn always sees the latest refined positions cleanly.
    n = len(ok_turns)
    weights = [2 if idx >= n // 2 else 1 for idx in range(n)]
    total_w = sum(weights)
    blocks: list[str] = []
    for t, w in zip(ok_turns, weights, strict=True):
        header_overhead = len(formatter(t, "")) + 4
        share = max(400, int((budget_chars * w) / total_w) - header_overhead)
        blocks.append(formatter(t, _clip_text(t.content, share)))
    return "\n\n".join(blocks)


def vote_guest_prompts(
    topic: str,
    guest_pool: list[str],
    participant: Participant,
    language: str,
) -> tuple[str, str]:
    system = (
        "You are participating in a multi-model Town Hall. "
        "Your first job is to vote for ONE complementary guest model from the exact "
        "candidate list below that will add the strongest independent perspective for this task. "
        "Reply ONLY with a JSON object with keys: "
        '{"primary": "<exact model id>", "secondary": "<different exact model id>", "reason": "<1 short sentence>"}.'
    )
    pool_lines = "\n".join(f"- {m}" for m in guest_pool)
    user = (
        f"You are {participant.name} ({participant.model}).\n"
        f"Topic / task:\n{topic}\n\n"
        f"Allowed guest models (pick exact IDs from this list only):\n{pool_lines}\n\n"
        f"{_lang_instruction(language)}"
    )
    return system, user


def independent_prompts(
    topic: str,
    participant: Participant,
    workspace: str,
    language: str,
) -> tuple[str, str]:
    persona_line = f" Focus especially on: {participant.persona}." if participant.persona else ""
    system = (
        "You are in Round 1 (blind independent analysis) of a multi-model Town Hall. "
        "Do NOT assume what other models will say. Inspect the topic (and workspace if provided) "
        "in strict read-only mode — never modify files or run mutating commands. "
        "Be specific, cite concrete files or evidence when available, and clearly separate "
        "strengths, gaps/risks, and prioritized recommendations."
        + persona_line
        + " "
        + _lang_instruction(language)
    )
    ws_note = f"\nWorkspace (read-only): {workspace}" if workspace else ""
    user = (
        f"Participant: {participant.name} ({participant.model}){ws_note}\n\n"
        f"Topic / task:\n{topic}\n\n"
        "Provide your independent structured analysis:\n"
        "1. Current state & strengths\n"
        "2. Gaps, missing pieces, or risks\n"
        "3. Top prioritized recommendations"
    )
    return system, user


def debate_prompts(
    topic: str,
    participant: Participant,
    round_num: int,
    own_previous: str,
    peer_turns: list[TurnResult],
    language: str,
) -> tuple[str, str]:
    system = (
        f"You are in Cross-Critique Round {round_num} of a multi-model Town Hall. "
        "Avoid sycophancy: do not simply agree with peers. Point out real blind spots, "
        "contradictions, or better alternatives, and honestly update your own stance when a "
        "peer gives stronger evidence. Stay strictly read-only. "
        + _lang_instruction(language)
    )
    own_budget = 4500
    peers_budget = max(4000, MAX_COMBINED_PROMPT_CHARS - len(system) - len(topic) - own_budget - 800)
    own_text = _clip_text(own_previous, own_budget) or "(no previous response)"
    peers_block = _budget_turns(
        peer_turns,
        budget_chars=peers_budget,
        formatter=lambda t, body: f"### {t.participant_name} ({t.model})\n{body}",
    )
    user = (
        f"Topic / task:\n{topic}\n\n"
        f"Your previous position ({participant.name}):\n{own_text}\n\n"
        f"Other participants' positions:\n{peers_block}\n\n"
        "Respond concisely with:\n"
        "1. Where you agree with peers\n"
        "2. What you challenge or correct (with reasoning)\n"
        "3. How your own prioritized recommendation changes (if at all)"
    )
    return system, user


def open_floor_prompts(
    topic: str,
    participant: Participant,
    turn_index: int,
    history: list[TurnResult],
    remaining_seconds: float,
    language: str,
) -> tuple[str, str]:
    system = (
        "You are participating in an open-floor Town Hall discussion with peer AI models. "
        "Add fresh value on your turn: build on prior points, raise concrete counter-arguments, "
        "or move the group toward a practical takeaway. Never repeat what was already said. "
        "Stay strictly read-only. "
        + _lang_instruction(language)
    )
    hist_budget = max(4000, MAX_COMBINED_PROMPT_CHARS - len(system) - len(topic) - 700)
    transcript = _budget_turns(
        history,
        budget_chars=hist_budget,
        formatter=lambda t, body: f"### Turn {t.round_num} — {t.participant_name} ({t.model})\n{body}",
    )
    hist_block = transcript if transcript else "(You are opening the floor.)"
    user = (
        f"Topic:\n{topic}\n\n"
        f"Conversation so far:\n{hist_block}\n\n"
        f"It is now Turn {turn_index} for {participant.name} ({participant.model}). "
        f"Approximate time remaining for the session: {max(0, int(remaining_seconds))}s.\n"
        "Contribute one focused, substantive message."
    )
    return system, user


def synthesis_prompts(
    topic: str,
    moderator: Participant,
    turns: list[TurnResult],
    guest_selection: ConsensusSelection | None,
    budget_reached: bool,
    language: str,
) -> tuple[str, str]:
    system = (
        "You are the Moderator closing a multi-model Town Hall. "
        "Synthesize the full transcript faithfully — do NOT hide meaningful minority disagreements. "
        "Reply ONLY with a JSON object matching this exact schema:\n"
        "{\n"
        '  "summary": "2-4 sentence executive summary of the deliberation",\n'
        '  "conclusion": "clear final consensus / recommendation",\n'
        '  "agreements": ["point agreed by the group", "..."],\n'
        '  "disagreements": ["open disagreement or minority concern", "..."],\n'
        '  "action_items": ["concrete prioritized next step", "..."]\n'
        "}\n"
        + _lang_instruction(language)
    )
    guest_block = ""
    if guest_selection is not None:
        vote_lines = "\n".join(
            f"- {v.voter_name}: primary={v.primary or 'invalid'}"
            + (f", secondary={v.secondary}" if v.secondary else "")
            + (f" — {v.reason}" if v.reason else "")
            for v in guest_selection.votes
        )
        guest_block = (
            f"\nConsensus guest selection: {guest_selection.chosen_model} "
            f"(method: {guest_selection.method})\n{vote_lines}\n"
        )
    substantive = [t for t in turns if t.ok and t.phase != "vote_guest"]
    transcript_budget = max(
        6000, MAX_COMBINED_PROMPT_CHARS - len(system) - len(topic) - len(guest_block) - 700
    )
    transcript_block = _budget_turns(
        substantive,
        budget_chars=transcript_budget,
        formatter=lambda t, body: f"### [{t.phase} #{t.round_num}] {t.participant_name} ({t.model})\n{body}",
    )
    budget_note = (
        "\nNote: The session time budget was reached before all planned rounds completed; "
        "synthesize cleanly from the turns captured so far.\n"
        if budget_reached
        else ""
    )
    user = (
        f"Moderator: {moderator.name} ({moderator.model})\n"
        f"Topic / task:\n{topic}\n"
        f"{guest_block}{budget_note}\n"
        f"Full transcript:\n{transcript_block}\n\n"
        "Return the closing JSON report."
    )
    return system, user
