"""Town Hall — open-source multi-agent deliberation engine."""

from .adapter import CallableAdapter, DemoAdapter, ParticipantAdapter
from .consensus import parse_guest_vote, resolve_consensus
from .engine import TownHallEngine
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
from .store import TownHallStore, render_markdown

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "CallableAdapter",
    "Conclusion",
    "ConsensusSelection",
    "DemoAdapter",
    "GuestVote",
    "Participant",
    "ParticipantAdapter",
    "SessionConfig",
    "SessionEvent",
    "SessionRecord",
    "TownHallEngine",
    "TownHallStore",
    "TurnRequest",
    "TurnResult",
    "parse_guest_vote",
    "render_markdown",
    "resolve_consensus",
]
