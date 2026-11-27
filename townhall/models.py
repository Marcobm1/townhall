"""Data models for Town Hall sessions, participants, votes, turns, and conclusions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SessionMode = Literal["council", "open_floor"]
SessionStatus = Literal["running", "completed", "cancelled", "failed"]


@dataclass(frozen=True)
class Participant:
    """One AI model sitting at the Town Hall table."""

    id: str
    name: str
    provider: str
    model: str
    role: str = "member"          # "core" | "guest" | "member"
    effort: str = ""
    persona: str = ""             # optional perspective hint (e.g. "architecture", "edge cases")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "role": self.role,
            "effort": self.effort,
            "persona": self.persona,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Participant":
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            provider=str(data["provider"]),
            model=str(data["model"]),
            role=str(data.get("role", "member")),
            effort=str(data.get("effort", "")),
            persona=str(data.get("persona", "")),
        )


@dataclass(frozen=True)
class TurnRequest:
    """A single read-only prompt sent to one participant."""

    session_id: str
    phase: str                    # "vote_guest" | "independent" | "debate" | "open_floor" | "synthesis"
    round_num: int
    participant: Participant
    system_prompt: str
    user_prompt: str
    workspace: str = ""
    timeout_seconds: float = 120.0
    read_only: bool = True


@dataclass(frozen=True)
class TurnResult:
    """The output produced by a participant for one turn."""

    participant_id: str
    participant_name: str
    model: str
    phase: str
    round_num: int
    content: str
    duration_seconds: float = 0.0
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    created_at: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.content.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "participant_name": self.participant_name,
            "model": self.model,
            "phase": self.phase,
            "round_num": self.round_num,
            "content": self.content,
            "duration_seconds": round(self.duration_seconds, 3),
            "error": self.error,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class GuestVote:
    """One core model's vote for the 4th guest participant."""

    voter_id: str
    voter_name: str
    primary: str
    secondary: str = ""
    reason: str = ""
    raw_content: str = ""
    valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "voter_id": self.voter_id,
            "voter_name": self.voter_name,
            "primary": self.primary,
            "secondary": self.secondary,
            "reason": self.reason,
            "valid": self.valid,
        }


@dataclass(frozen=True)
class ConsensusSelection:
    """Outcome of the Phase 0 guest-model election."""

    chosen_model: str
    method: str                   # "unanimous" | "majority" | "borda" | "tie_break" | "fallback"
    votes: list[GuestVote] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chosen_model": self.chosen_model,
            "method": self.method,
            "votes": [v.to_dict() for v in self.votes],
            "scores": dict(self.scores),
        }


@dataclass(frozen=True)
class Conclusion:
    """Structured final output of a Town Hall session."""

    moderator_id: str
    moderator_name: str
    summary: str
    conclusion: str
    agreements: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    raw_content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "moderator_id": self.moderator_id,
            "moderator_name": self.moderator_name,
            "summary": self.summary,
            "conclusion": self.conclusion,
            "agreements": list(self.agreements),
            "disagreements": list(self.disagreements),
            "action_items": list(self.action_items),
        }


@dataclass(frozen=True)
class SessionConfig:
    """Configuration and hard safety/cost budgets for one Town Hall session."""

    topic: str
    participants: list[Participant]
    mode: SessionMode = "council"
    workspace: str = ""
    guest_pool: list[str] = field(default_factory=list)
    guest_provider: str = "opencode"
    debate_rounds: int = 1
    max_turns: int = 8            # used in open_floor mode
    max_duration_seconds: float = 600.0
    turn_timeout_seconds: float = 120.0
    moderator_id: str = ""        # empty => first core participant
    language: str = "es"
    isolate_workspace: bool = True

    def __post_init__(self) -> None:
        if not self.topic or not self.topic.strip():
            raise ValueError("topic must not be empty")
        if len(self.participants) < 2:
            raise ValueError("at least 2 participants are required for a Town Hall")
        if self.mode not in ("council", "open_floor"):
            raise ValueError(f"unsupported mode: {self.mode!r}")
        if self.debate_rounds < 0 or self.debate_rounds > 3:
            raise ValueError("debate_rounds must be between 0 and 3")
        if self.max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        if self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be > 0")
        if self.turn_timeout_seconds <= 0:
            raise ValueError("turn_timeout_seconds must be > 0")
        ids = [p.id for p in self.participants]
        if len(set(ids)) != len(ids):
            raise ValueError("participant ids must be unique")


@dataclass(frozen=True)
class SessionEvent:
    """Real-time progress event emitted during a session (ready for CLI or SSE)."""

    type: str
    session_id: str
    timestamp: float
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }


@dataclass
class SessionRecord:
    """Complete durable record of a Town Hall session."""

    id: str
    topic: str
    mode: SessionMode
    status: SessionStatus
    workspace: str
    language: str
    participants: list[Participant]
    started_at: float
    finished_at: float = 0.0
    guest_selection: ConsensusSelection | None = None
    turns: list[TurnResult] = field(default_factory=list)
    conclusion: Conclusion | None = None
    budget_reached: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "mode": self.mode,
            "status": self.status,
            "workspace": self.workspace,
            "language": self.language,
            "participants": [p.to_dict() for p in self.participants],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "guest_selection": self.guest_selection.to_dict() if self.guest_selection else None,
            "turns": [t.to_dict() for t in self.turns],
            "conclusion": self.conclusion.to_dict() if self.conclusion else None,
            "budget_reached": self.budget_reached,
            "error": self.error,
        }


@dataclass(frozen=True)
class ScheduleSpec:
    """Configuration for a recurring Town Hall session (e.g. weekly project review)."""

    id: str
    name: str
    topic: str
    participants: list[Participant]
    mode: SessionMode = "council"
    workspace: str = ""
    guest_pool: list[str] = field(default_factory=list)
    guest_provider: str = "opencode"
    debate_rounds: int = 1
    max_turns: int = 8
    max_duration_seconds: float = 600.0
    turn_timeout_seconds: float = 180.0
    moderator_id: str = ""
    language: str = "es"
    interval_hours: float = 168.0       # default: weekly (7 * 24h)
    allowed_hours: str = ""             # e.g. "09:00-21:00" (empty = anytime)
    enabled: bool = True
    last_run_at: float = 0.0
    last_session_id: str = ""
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("schedule name must not be empty")
        if not self.topic or not self.topic.strip():
            raise ValueError("schedule topic must not be empty")
        if self.interval_hours <= 0:
            raise ValueError("interval_hours must be > 0")
        if len(self.participants) < 2:
            raise ValueError("at least 2 participants are required")

    def to_session_config(self) -> SessionConfig:
        return SessionConfig(
            topic=self.topic,
            participants=list(self.participants),
            mode=self.mode,
            workspace=self.workspace,
            guest_pool=list(self.guest_pool),
            guest_provider=self.guest_provider,
            debate_rounds=self.debate_rounds,
            max_turns=self.max_turns,
            max_duration_seconds=self.max_duration_seconds,
            turn_timeout_seconds=self.turn_timeout_seconds,
            moderator_id=self.moderator_id,
            language=self.language,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "topic": self.topic,
            "mode": self.mode,
            "workspace": self.workspace,
            "participants": [p.to_dict() for p in self.participants],
            "guest_pool": list(self.guest_pool),
            "guest_provider": self.guest_provider,
            "debate_rounds": self.debate_rounds,
            "max_turns": self.max_turns,
            "max_duration_seconds": self.max_duration_seconds,
            "turn_timeout_seconds": self.turn_timeout_seconds,
            "moderator_id": self.moderator_id,
            "language": self.language,
            "interval_hours": self.interval_hours,
            "allowed_hours": self.allowed_hours,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at,
            "last_session_id": self.last_session_id,
            "created_at": self.created_at,
        }
