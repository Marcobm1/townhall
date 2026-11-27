"""Town Hall deliberation engine supporting `council` and `open_floor` protocols."""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from . import prompts
from .adapter import ParticipantAdapter
from .consensus import extract_json_object, parse_guest_vote, resolve_consensus
from .models import (
    Conclusion,
    ConsensusSelection,
    GuestVote,
    Participant,
    SessionConfig,
    SessionEvent,
    SessionRecord,
    TurnRequest,
    TurnResult,
)
from .sandbox import disposable_worktree

EventCallback = Callable[[SessionEvent], None]
ClockFn = Callable[[], float]


def _make_guest_participant(model_id: str, provider: str) -> Participant:
    short = model_id.rsplit("/", 1)[-1]
    return Participant(
        id="guest",
        name=f"Guest ({short})",
        provider=provider,
        model=model_id,
        role="guest",
    )


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def parse_conclusion(moderator: Participant, raw_content: str) -> Conclusion:
    """Parse moderator JSON report, falling back cleanly to plain prose when needed."""
    data = extract_json_object(raw_content)
    if data:
        summary = str(data.get("summary") or "").strip()
        conclusion = str(data.get("conclusion") or "").strip()
        if summary or conclusion:
            return Conclusion(
                moderator_id=moderator.id,
                moderator_name=moderator.name,
                summary=summary or conclusion,
                conclusion=conclusion or summary,
                agreements=_str_list(data.get("agreements")),
                disagreements=_str_list(data.get("disagreements")),
                action_items=_str_list(data.get("action_items")),
                raw_content=raw_content,
            )
    text = (raw_content or "").strip()
    first_para = text.split("\n\n", 1)[0].strip() if text else ""
    return Conclusion(
        moderator_id=moderator.id,
        moderator_name=moderator.name,
        summary=first_para[:400],
        conclusion=text,
        raw_content=raw_content,
    )


class TownHallEngine:
    """Orchestrates multi-model deliberations with hard budgets and streaming events."""

    def __init__(
        self,
        adapter: ParticipantAdapter | dict[str, ParticipantAdapter],
        *,
        on_event: EventCallback | None = None,
        clock: ClockFn | None = None,
    ):
        self._adapters = adapter
        self._on_event = on_event
        self._clock: ClockFn = clock or time.monotonic

    def _adapter_for(self, participant: Participant) -> ParticipantAdapter:
        if isinstance(self._adapters, dict):
            if participant.id in self._adapters:
                return self._adapters[participant.id]
            if participant.provider in self._adapters:
                return self._adapters[participant.provider]
            if "default" in self._adapters:
                return self._adapters["default"]
            raise KeyError(
                f"No adapter registered for participant {participant.id!r} "
                f"or provider {participant.provider!r}"
            )
        return self._adapters

    def _emit(self, session_id: str, event_type: str, **payload) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(
                SessionEvent(
                    type=event_type,
                    session_id=session_id,
                    timestamp=time.time(),
                    payload=payload,
                )
            )
        except Exception:
            # Observers must never crash a deliberation.
            pass

    def _run_one(self, request: TurnRequest) -> TurnResult:
        self._emit(
            request.session_id,
            "turn_started",
            phase=request.phase,
            round_num=request.round_num,
            participant_id=request.participant.id,
            participant_name=request.participant.name,
            model=request.participant.model,
        )
        t0 = self._clock()
        try:
            res = self._adapter_for(request.participant).run_turn(request)
        except Exception as exc:
            res = TurnResult(
                participant_id=request.participant.id,
                participant_name=request.participant.name,
                model=request.participant.model,
                phase=request.phase,
                round_num=request.round_num,
                content="",
                duration_seconds=max(0.0, self._clock() - t0),
                error=str(exc) or exc.__class__.__name__,
                created_at=time.time(),
            )
        self._emit(
            request.session_id,
            "turn_completed",
            phase=res.phase,
            round_num=res.round_num,
            participant_id=res.participant_id,
            participant_name=res.participant_name,
            ok=res.ok,
            error=res.error,
            duration_seconds=round(res.duration_seconds, 3),
        )
        return res

    def _run_parallel(self, requests: list[TurnRequest]) -> list[TurnResult]:
        if not requests:
            return []
        if len(requests) == 1:
            return [self._run_one(requests[0])]
        results_by_index: dict[int, TurnResult] = {}
        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            future_to_idx = {
                pool.submit(self._run_one, req): idx for idx, req in enumerate(requests)
            }
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                results_by_index[idx] = fut.result()
        return [results_by_index[i] for i in range(len(requests))]

    def _pick_moderator(self, config: SessionConfig, participants: list[Participant]) -> Participant:
        if config.moderator_id:
            for p in participants:
                if p.id == config.moderator_id:
                    return p
        return participants[0]

    def run(
        self,
        config: SessionConfig,
        *,
        session_id: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SessionRecord:
        sid = session_id or uuid.uuid4().hex[:12]
        cancel = cancel_event or threading.Event()
        with disposable_worktree(config.workspace, enabled=config.isolate_workspace) as ws:
            return self._run_in_workspace(config, sid, cancel, ws.path, ws.isolated)

    def _run_in_workspace(
        self,
        config: SessionConfig,
        sid: str,
        cancel: threading.Event,
        effective_workspace: str,
        workspace_isolated: bool,
    ) -> SessionRecord:
        start_mono = self._clock()
        deadline = start_mono + config.max_duration_seconds

        participants = list(config.participants)
        record = SessionRecord(
            id=sid,
            topic=config.topic.strip(),
            mode=config.mode,
            status="running",
            workspace=config.workspace,
            language=config.language,
            participants=participants,
            started_at=time.time(),
        )
        self._emit(
            sid,
            "session_started",
            mode=config.mode,
            topic=record.topic,
            workspace_isolated=workspace_isolated,
            participants=[p.to_dict() for p in participants],
        )

        def remaining() -> float:
            return max(0.0, deadline - self._clock())

        def turn_timeout() -> float:
            rem = remaining()
            return min(config.turn_timeout_seconds, rem) if rem > 0 else config.turn_timeout_seconds

        def budget_exhausted() -> bool:
            return self._clock() >= deadline

        def mark_cancelled() -> SessionRecord:
            record.status = "cancelled"
            record.finished_at = time.time()
            self._emit(sid, "session_cancelled")
            return record

        # -------------------------------------------- Phase 0: Consensus 4th guest
        if config.guest_pool:
            if cancel.is_set():
                return mark_cancelled()
            self._emit(sid, "phase_started", phase="vote_guest", round_num=0)
            vote_reqs = []
            for p in participants:
                sys_p, usr_p = prompts.vote_guest_prompts(
                    record.topic, config.guest_pool, p, config.language
                )
                vote_reqs.append(
                    TurnRequest(
                        session_id=sid,
                        phase="vote_guest",
                        round_num=0,
                        participant=p,
                        system_prompt=sys_p,
                        user_prompt=usr_p,
                        workspace=effective_workspace,
                        timeout_seconds=turn_timeout(),
                        read_only=True,
                    )
                )
            vote_results = self._run_parallel(vote_reqs)
            record.turns.extend(vote_results)

            ballots: list[GuestVote] = [
                parse_guest_vote(r.participant_id, r.participant_name, r.content, config.guest_pool)
                for r in vote_results
            ]
            selection: ConsensusSelection = resolve_consensus(ballots, config.guest_pool)
            record.guest_selection = selection
            guest = _make_guest_participant(selection.chosen_model, config.guest_provider)
            participants.append(guest)
            self._emit(
                sid,
                "guest_selected",
                chosen_model=selection.chosen_model,
                method=selection.method,
                scores=selection.scores,
            )

        if cancel.is_set():
            return mark_cancelled()

        # -------------------------------------------- Main deliberation
        if config.mode == "council":
            self._run_council_phases(
                config, record, participants, effective_workspace, cancel, deadline, turn_timeout
            )
        else:
            self._run_open_floor(
                config, record, participants, effective_workspace, cancel, deadline, remaining, turn_timeout
            )

        if cancel.is_set():
            return mark_cancelled()

        if budget_exhausted() and not record.budget_reached:
            record.budget_reached = True
            self._emit(sid, "budget_reached", phase="pre_synthesis")

        # -------------------------------------------- Final synthesis
        substantive = [t for t in record.turns if t.ok and t.phase != "vote_guest"]
        if not substantive:
            record.status = "failed"
            record.error = "No participant produced a valid response."
            record.finished_at = time.time()
            self._emit(sid, "session_failed", error=record.error)
            return record

        moderator = self._pick_moderator(config, participants)
        self._emit(sid, "phase_started", phase="synthesis", round_num=1, moderator_id=moderator.id)
        sys_p, usr_p = prompts.synthesis_prompts(
            record.topic,
            moderator,
            record.turns,
            record.guest_selection,
            record.budget_reached,
            config.language,
        )
        synth_res = self._run_one(
            TurnRequest(
                session_id=sid,
                phase="synthesis",
                round_num=1,
                participant=moderator,
                system_prompt=sys_p,
                user_prompt=usr_p,
                workspace=effective_workspace,
                timeout_seconds=config.turn_timeout_seconds,
                read_only=True,
            )
        )
        record.turns.append(synth_res)
        if synth_res.ok:
            record.conclusion = parse_conclusion(moderator, synth_res.content)
        else:
            # Never lose the deliberation if only the moderator failed.
            fallback_text = "\n\n".join(
                f"{t.participant_name}: {t.content.strip()[:240]}" for t in substantive[-len(participants):]
            )
            record.conclusion = Conclusion(
                moderator_id=moderator.id,
                moderator_name=moderator.name,
                summary=f"Moderator synthesis failed ({synth_res.error}); showing latest positions.",
                conclusion=fallback_text,
            )

        record.status = "completed"
        record.finished_at = time.time()
        self._emit(
            sid,
            "session_completed",
            status=record.status,
            budget_reached=record.budget_reached,
            turn_count=len(record.turns),
        )
        return record

    def _run_council_phases(
        self,
        config: SessionConfig,
        record: SessionRecord,
        participants: list[Participant],
        effective_workspace: str,
        cancel: threading.Event,
        deadline: float,
        turn_timeout: Callable[[], float],
    ) -> None:
        # Phase 1: Blind parallel independent analysis
        if self._clock() >= deadline:
            record.budget_reached = True
            self._emit(record.id, "budget_reached", phase="independent")
            return
        self._emit(record.id, "phase_started", phase="independent", round_num=1)
        indep_reqs = []
        for p in participants:
            sys_p, usr_p = prompts.independent_prompts(
                record.topic, p, effective_workspace, config.language
            )
            indep_reqs.append(
                TurnRequest(
                    session_id=record.id,
                    phase="independent",
                    round_num=1,
                    participant=p,
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    workspace=effective_workspace,
                    timeout_seconds=turn_timeout(),
                    read_only=True,
                )
            )
        latest_round = self._run_parallel(indep_reqs)
        record.turns.extend(latest_round)

        # Phase 2: N parallel cross-critique rounds
        for rnum in range(1, config.debate_rounds + 1):
            if cancel.is_set():
                return
            if self._clock() >= deadline:
                record.budget_reached = True
                self._emit(record.id, "budget_reached", phase="debate", round_num=rnum)
                return
            self._emit(record.id, "phase_started", phase="debate", round_num=rnum)
            by_pid = {t.participant_id: t for t in latest_round}
            debate_reqs = []
            for p in participants:
                own = by_pid.get(p.id)
                peers = [t for t in latest_round if t.participant_id != p.id and t.ok]
                sys_p, usr_p = prompts.debate_prompts(
                    record.topic,
                    p,
                    rnum,
                    own.content if own and own.ok else "",
                    peers,
                    config.language,
                )
                debate_reqs.append(
                    TurnRequest(
                        session_id=record.id,
                        phase="debate",
                        round_num=rnum,
                        participant=p,
                        system_prompt=sys_p,
                        user_prompt=usr_p,
                        workspace=effective_workspace,
                        timeout_seconds=turn_timeout(),
                        read_only=True,
                    )
                )
            latest_round = self._run_parallel(debate_reqs)
            record.turns.extend(latest_round)

    def _run_open_floor(
        self,
        config: SessionConfig,
        record: SessionRecord,
        participants: list[Participant],
        effective_workspace: str,
        cancel: threading.Event,
        deadline: float,
        remaining: Callable[[], float],
        turn_timeout: Callable[[], float],
    ) -> None:
        self._emit(record.id, "phase_started", phase="open_floor", round_num=1)
        floor_history: list[TurnResult] = []
        for idx in range(config.max_turns):
            if cancel.is_set():
                return
            if self._clock() >= deadline:
                record.budget_reached = True
                self._emit(record.id, "budget_reached", phase="open_floor", round_num=idx + 1)
                return
            speaker = participants[idx % len(participants)]
            sys_p, usr_p = prompts.open_floor_prompts(
                record.topic,
                speaker,
                idx + 1,
                floor_history,
                remaining(),
                config.language,
            )
            res = self._run_one(
                TurnRequest(
                    session_id=record.id,
                    phase="open_floor",
                    round_num=idx + 1,
                    participant=speaker,
                    system_prompt=sys_p,
                    user_prompt=usr_p,
                    workspace=effective_workspace,
                    timeout_seconds=turn_timeout(),
                    read_only=True,
                )
            )
            record.turns.append(res)
            if res.ok:
                floor_history.append(res)
