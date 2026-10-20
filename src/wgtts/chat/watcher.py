"""Watch only the live tail; scrolling into old history must not speak it."""

import time
from hashlib import sha256
from threading import Event, Thread

from wgtts.chat.models import Message


class ReplyTracker:
    def __init__(self):
        self.reset()

    def reset(self):
        self.session = None
        self.turn = None
        self.armed = False
        self.emitted = False
        self.fingerprint = None
        self.since = 0

    def observe(self, snapshot, now, stable=2.5):
        # The app's global user counter distinguishes a newly sent prompt from
        # old messages remounted by virtualization. Be conservative without it.
        if snapshot.expected_users is None or not snapshot.messages:
            return None
        turn = snapshot.expected_users
        at_tail = snapshot.scroll_percent < 0 or snapshot.scroll_percent >= 99.5
        if self.session != snapshot.session_key:
            self.reset()
            self.session, self.turn = snapshot.session_key, turn
            self.armed = at_tail and snapshot.generating
            return None
        if turn != self.turn:
            self.turn = turn
            self.armed = turn > 0
            self.emitted = False
            self.fingerprint = None
        if not at_tail:
            self.fingerprint = None
            return None
        if snapshot.generating:
            if not self.emitted:
                self.armed = True
            self.fingerprint = None
            return None
        if not self.armed or self.emitted:
            return None
        # Multiple assistant blocks can be interim commentary. Speak only the
        # final completed block, after generation has stopped and text is stable.
        message = snapshot.messages[-1]
        if message.role != "assistant" or not message.complete:
            self.fingerprint = None
            return None
        fingerprint = sha256(message.text.encode("utf-8")).hexdigest()
        if fingerprint != self.fingerprint:
            self.fingerprint, self.since = fingerprint, now
            return None
        if now - self.since < stable:
            return None
        self.emitted = True
        return Message(message.role, message.text, True, message.source_key)


class AutoReader:
    def __init__(self, settings, read_lock, on_reply, notify=print):
        self.settings = settings
        self.read_lock = read_lock
        self.on_reply = on_reply
        self.notify = notify
        self.enabled = Event()
        self.closed = Event()
        self.reset_requested = Event()
        self.status = "выключено"
        self.thread = Thread(target=self._run, name="chat-watch", daemon=True)
        self.thread.start()

    def set_enabled(self, enabled):
        self.reset_requested.set()
        self.status = "жду чат" if enabled else "приостановлено"
        (self.enabled.set if enabled else self.enabled.clear)()

    def close(self):
        self.enabled.clear()
        self.closed.set()
        self.thread.join(timeout=2)

    def _run(self):
        from comtypes import CoInitializeEx, CoUninitialize

        from wgtts.chat.reader import ChatReader

        CoInitializeEx(0)
        reader = ChatReader()
        tracker = ReplyTracker()
        last_error = None
        try:
            while not self.closed.wait(self.settings.data["poll"]):
                if not self.enabled.is_set():
                    continue
                if self.reset_requested.is_set():
                    tracker.reset()
                    self.reset_requested.clear()
                if not self.read_lock.acquire(blocking=False):
                    continue
                try:
                    snapshot = reader.snapshot()
                except Exception as error:
                    if str(error) != last_error:
                        self.notify(f"Автоозвучка ждёт чат: {error}")
                        last_error = str(error)
                    continue
                finally:
                    self.read_lock.release()
                last_error = None
                if self.enabled.is_set():
                    self.status = f"{snapshot.title}: " + (
                        "ответ ещё пишется"
                        if snapshot.generating
                        else "видна старая часть чата, прокрути вниз"
                        if 0 <= snapshot.scroll_percent < 99.5
                        else "жду новый завершённый ответ"
                    )
                message = tracker.observe(snapshot, time.monotonic(), self.settings.data["stable"])
                if (
                    message
                    and self.enabled.is_set()
                    and not self.reset_requested.is_set()
                    and not self.closed.is_set()
                ):
                    self.on_reply(snapshot, message)
        finally:
            CoUninitialize()
