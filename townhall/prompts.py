"""Prompt builders for each Town Hall deliberation phase."""

from __future__ import annotations

from .models import ConsensusSelection, Participant, TurnResult

_LANG_NOTE = {
    "es": "Respond in Spanish (clear, concise, technical).",
    "en": "Respond in English (clear, concise, technical).",
}


def _lang_instruction(lang: str) -> str:
    return _LANG_NOTE.get((lang or "en").lower()[:2], _LANG_NOTE["en"])


def vote_guest_prompts(
    topic: str,
    guest_pool: list[str],
    participant: Participant,
    language: str,
) -> tuple[str, str]:
    system = (
        "You are participating in a multi-model Town Hall. "
        "Your first job is to vote for ONE complementary 4th guest model from the exact "
        "candidate list below that will add the strongest independent perspective for this task. "
        "Reply ONLY with a JSON object with keys: "
        '{"primary": "<exact model id>", "secondary": "<different exact model id>", "reason": "<1 short sentence>"}.'
    )
    pool_lines = "\n".join(f"- {m}" for m in guest_pool)
    user = (
        f"You are {participant.name} ({participant.model}).\n"
        f"Topic / task:\n{topic}\n\n"
        f"Allowed 4th guest models (pick exact IDs from this list only):\n{pool_lines}\n\n"
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
    peers_block = "\n\n".join(
        f"### {t.participant_name} ({t.model})\n{t.content.strip()}"
        for t in peer_turns
        if t.ok
    )
    user = (
        f"Topic / task:\n{topic}\n\n"
        f"Your previous position ({participant.name}):\n{own_previous.strip() or '(no previous response)'}\n\n"
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
    transcript = "\n\n".join(
        f"### Turn {t.round_num} — {t.participant_name} ({t.model})\n{t.content.strip()}"
        for t in history
        if t.ok
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
            f"\n4th guest selection: {guest_selection.chosen_model} "
            f"(method: {guest_selection.method})\n{vote_lines}\n"
        )
    transcript_block = "\n\n".join(
        f"### [{t.phase} #{t.round_num}] {t.participant_name} ({t.model})\n{t.content.strip()}"
        for t in turns
        if t.ok and t.phase != "vote_guest"
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
