"""Plain data only: no live Windows elements are stored in the history."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class Message:
    role: str
    text: str
    complete: bool = True
    source_key: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def signature(self):
        return self.role, self.text


@dataclass
class Snapshot:
    title: str
    session_key: str
    messages: list[Message]
    scroll_percent: float
    view_size: float
    expected_users: int | None = None
    generating: bool = False


@dataclass
class ScanResult:
    title: str
    messages: list[Message]
    reached_top: bool = False
    reached_bottom: bool = False
    expected_users: int | None = None
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_key: str | None = None

    @property
    def user_count(self):
        return sum(message.role == "user" for message in self.messages)

    @property
    def complete(self):
        return (
            self.reached_top
            and self.reached_bottom
            and not self.warnings
            and all(message.complete for message in self.messages)
            and self.expected_users is not None
            and self.user_count == self.expected_users
        )

    def to_dict(self):
        return {"schema_version": 1, **asdict(self), "complete": self.complete}

    @classmethod
    def from_dict(cls, data):
        return cls(
            title=data["title"],
            messages=[Message(**item) for item in data["messages"]],
            reached_top=data.get("reached_top", False),
            reached_bottom=data.get("reached_bottom", False),
            expected_users=data.get("expected_users"),
            warnings=data.get("warnings", []),
            created_at=data["created_at"],
            session_key=data.get("session_key"),
        )
