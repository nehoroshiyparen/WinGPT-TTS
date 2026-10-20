"""Merge overlapping windows in order; identical replies are not a set."""

import json
import os
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from wgtts.chat.models import Message, ScanResult, Snapshot


class HistoryGapError(RuntimeError):
    pass


def merge_window(history: list[Message], incoming: list[Message]) -> int:
    """Append only a chronological continuation, never silently join a gap."""
    if not incoming:
        return 0
    if not history:
        history.extend(incoming)
        return len(incoming)

    # UIA runtime IDs are useful within one live scan, not as persistent IDs.
    old_keys = {
        message.source_key: index for index, message in enumerate(history) if message.source_key
    }
    offsets = {
        old_keys[message.source_key] - index
        for index, message in enumerate(incoming)
        if message.source_key and message.source_key in old_keys
    }
    if len(offsets) == 1:
        offset = offsets.pop()
        if offset >= 0 and offset <= len(history):
            overlap = min(len(incoming), len(history) - offset)
            if all(
                history[offset + n].signature == incoming[n].signature
                or history[offset + n].source_key == incoming[n].source_key
                for n in range(overlap)
            ):
                for n in range(overlap):
                    old, new = history[offset + n], incoming[n]
                    # A draft can grow; retain the stable local selection ID.
                    new.id = old.id
                    history[offset + n] = new
                added = incoming[overlap:]
                history.extend(added)
                return len(added)

    old = [message.signature for message in history]
    new = [message.signature for message in incoming]
    for size in range(min(len(old), len(new)), 0, -1):
        if old[-size:] == new[:size]:
            if (
                size == len(new)
                and all(message.source_key for message in incoming)
                and all(message.source_key for message in history[-size:])
                and not any(message.source_key in old_keys for message in incoming)
            ):
                raise HistoryGapError(
                    "Неоднозначный повтор: одинаковый текст с новыми UIA-идентификаторами."
                )
            history.extend(incoming[size:])
            return len(new) - size
    # Scrolling within an already observed region is not another copy of it.
    for start in range(len(old) - len(new) + 1):
        if old[start : start + len(new)] == new:
            return 0
    raise HistoryGapError("Между снимками нет перекрытия; полнота истории не подтверждена.")


def refresh_history(history: ScanResult | None, snapshot: Snapshot):
    """Refresh selectable history and return the snapshot's absolute indexes."""
    if not snapshot.messages:
        return history, [], None
    incoming = deepcopy(snapshot.messages)
    same_session = history is not None and history.session_key == snapshot.session_key
    # Migrate old caches without a session key only when a unique user+answer
    # sequence at their tail proves a continuation. A title alone is not enough.
    if history and history.session_key is None and history.title == snapshot.title:
        old = [item.signature for item in history.messages]
        new = [item.signature for item in incoming]
        for count in range(min(len(old), len(new)), 1, -1):
            prefix = new[:count]
            if old[-count:] == prefix and {role for role, _ in prefix} == {"user", "assistant"}:
                occurrences = sum(
                    old[start : start + count] == prefix for start in range(len(old) - count + 1)
                )
                same_session = occurrences == 1
                break
    note = None
    if same_session:
        merged = deepcopy(history)
        try:
            merge_window(merged.messages, incoming)
            candidates = [
                start
                for start in range(len(merged.messages) - len(incoming) + 1)
                if all(
                    merged.messages[start + n].signature == item.signature
                    for n, item in enumerate(incoming)
                )
            ]
            scores = [
                sum(
                    bool(item.source_key)
                    and item.source_key == merged.messages[start + n].source_key
                    for n, item in enumerate(incoming)
                )
                for start in candidates
            ]
            if not candidates or scores.count(max(scores)) != 1:
                raise HistoryGapError("Неоднозначное положение прочитанных сообщений.")
            start = candidates[scores.index(max(scores))]
            indexes = list(range(start, start + len(incoming)))
            for index, item in zip(indexes, incoming):
                item.id = merged.messages[index].id
                merged.messages[index] = item
            merged.session_key = snapshot.session_key
            if snapshot.expected_users is not None:
                merged.expected_users = snapshot.expected_users
            return merged, indexes, None
        except HistoryGapError:
            note = "Не удалось соединить снимки. Выбран доступный фрагмент; старые импорты сохранены. Для полного чтения: scan."
    result = ScanResult(
        snapshot.title,
        incoming,
        snapshot.scroll_percent <= 0.05,
        snapshot.scroll_percent < 0 or snapshot.scroll_percent >= 99.95,
        snapshot.expected_users,
        session_key=snapshot.session_key,
    )
    if not result.reached_top or not result.reached_bottom:
        result.warnings.append("Прочитана загруженная часть чата. Для полной истории: scan.")
    return result, list(range(len(incoming))), note


class HistoryStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def save(self, result: ScanResult) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        content = json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        identity = result.session_key or (result.title + "\0" + result.created_at)
        key = sha256(identity.encode("utf-8")).hexdigest()[:24]
        path = self.directory / ("chat-" + key + ".json")
        self._write(path, content)
        # A partial read must not erase the last confirmed complete history.
        # Replace these files in place instead of archiving a growing chat for
        # every new reply (which took quadratic space over a long conversation).
        if result.complete:
            self._write(self.directory / ("full-" + key + ".json"), content)
        self._write(self.directory / "latest.json", content)
        return path

    def _write(self, path: Path, content: bytes):
        if path.exists() and path.read_bytes() == content:
            return
        temporary = self.directory / (".save-" + uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self) -> ScanResult | None:
        path = self.directory / "latest.json"
        if not path.exists():
            return None
        return ScanResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
