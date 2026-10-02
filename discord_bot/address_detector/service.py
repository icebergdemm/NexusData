from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .context import AddressContext
from .filter import TriggerMatch


class ReactionPort(Protocol):
    async def add(self, message: object) -> None: ...
    async def remove(self, message: object) -> None: ...


@dataclass
class DetectorJob:
    message: object
    match: TriggerMatch | None = None
    revision: int = 1
    candidate_context: AddressContext | None = None
    context: AddressContext | None = None

    @property
    def message_id(self) -> int:
        return int(getattr(self.message, "id", getattr(self.message, "message_id", 0)))
