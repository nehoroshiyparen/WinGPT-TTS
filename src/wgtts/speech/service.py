"""One cancellable synthesis/playback queue shared by the terminal and widget."""

from collections import deque
from copy import deepcopy
from math import isfinite
from pathlib import Path
from threading import Condition, Event, Thread

from wgtts.chat.tables import tables_for_speech
from wgtts.speech.applio import ApplioClient, Cancelled
from wgtts.speech.cache import RecordingCache, recording_key
from wgtts.speech.playback import WavePlayer


class SpeechService:
    def __init__(
        self, settings, directory, notify=print, *, client_factory=ApplioClient, player=None
    ):
        self.settings = settings
        self.directory = Path(directory)
        self.notify = notify
        self.client_factory = client_factory
        self.player = player or WavePlayer(lambda: self.settings.data.get("volume", 100))
        self.recording = RecordingCache(self.directory, self.notify)
        self.condition = Condition()
        self.pending = deque()
        self.cancel = Event()
        self.closed = False
        self.state = "idle"
        self.epoch = 0
        self.cancel_revision = 0
        self.current = None
        self.worker = Thread(target=self._run, name="speech", daemon=True)
        self.worker.start()

    @property
    def busy(self):
        with self.condition:
            return bool(self.pending) or self.state in {"preparing", "playing"}

    def say(self, text, *, replace=True, expected_revision=None):
        if not text.strip():
            raise ValueError("В сообщении нет текста.")
        with self.condition:
            if self.closed or (
                expected_revision is not None and expected_revision != self.cancel_revision
            ):
                return
            if replace:
                self._stop_locked()
            self.pending.append((text, deepcopy(self.settings.data)))
            if replace or self.state != "playing":
                self.state = "preparing"
            self.condition.notify_all()

    @property
    def progress(self):
        with self.condition:
            return self.player.position, self.player.duration

    def _stop_locked(self, *, clear_pending=True):
        paused = self.state in {"playing", "paused"} and self.current is not None
        self.epoch += 1
        if clear_pending:
            self.pending.clear()
        self.cancel.set()
        self.player.stop()
        self.state = "paused" if paused else "idle"

    def stop(self):
        with self.condition:
            self.cancel_revision += 1
            self._stop_locked()

    def resume(self, *, expected_revision=None):
        with self.condition:
            if self.closed or (
                expected_revision is not None and expected_revision != self.cancel_revision
            ):
                return False
            if self.state != "paused" or self.current is None:
                return False
            self._stop_locked()
            self.pending.append(self.current)
            self.condition.notify_all()
            return True

    def seek(self, seconds, *, relative=False):
        if not isfinite(seconds):
            raise ValueError("Позиция должна быть конечным числом секунд.")
        with self.condition:
            if self.closed:
                return
            if self.state == "preparing" or (self.pending and self.state != "playing"):
                raise ValueError("Дождись окончания подготовки записи.")
            if self.current is None:
                raise ValueError("Нет записи для перемотки. Сначала запусти озвучку.")
            playing = self.state == "playing"
            # Interrupt the old playback timer as well as the audio stream.
            self._stop_locked(clear_pending=False)
            position = self.player.seek(seconds, relative=relative)
            self.state = "paused"
            if playing and position < self.player.duration:
                self.pending.appendleft(self.current)
                self.condition.notify_all()
            return position

    def restart(self, *, expected_revision=None):
        with self.condition:
            if (
                self.closed
                or self.current is None
                or self.state == "preparing"
                or (expected_revision is not None and expected_revision != self.cancel_revision)
            ):
                return False
            self._stop_locked()
            self.player.seek(0)
            self.pending.append(self.current)
            self.condition.notify_all()
            return True

    def close(self):
        with self.condition:
            self.closed = True
            self._stop_locked()
            self.condition.notify_all()
        self.worker.join(timeout=12)

    def _set_state(self, epoch, state):
        with self.condition:
            if epoch == self.epoch:
                self.state = state

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending or self.closed)
                if self.closed:
                    return
                text, settings = self.pending.popleft()
                epoch = self.epoch
                cancel = self.cancel = Event()
                self.state = "preparing"
            path = None
            try:
                key = recording_key(text, settings)
                path = self.recording.find(key)
                if path is None:
                    client = self.client_factory(settings["url"])
                    params = settings["tts"]
                    if not params.get("pthPath") or not params.get("ttsVoice"):
                        params = {**client.discover(), **params}
                    self.notify("Готовлю озвучку в Applio…")
                    path = client.synthesize(
                        tables_for_speech(text), params, self.directory, cancel
                    )
                with self.condition:
                    if cancel.is_set() or epoch != self.epoch:
                        continue
                    # Publish only a completed, still-current generation. Stop,
                    # playback failure and failed synthesis retain this recording.
                    if path != self.recording.path:
                        self.recording.replace(path, key)
                    self.current = text, settings
                    duration = self.player.start(path)
                    self.state = "playing"
                self.notify("Озвучиваю…")
                cancel.wait(duration)
                with self.condition:
                    if epoch == self.epoch:
                        self.player.stop()
                self._set_state(epoch, "idle")
            except Cancelled:
                self._set_state(epoch, "idle")
            except Exception as error:
                self._set_state(epoch, "error")
                if not cancel.is_set():
                    self.notify(f"Ошибка озвучки: {error}")
            finally:
                # Discard a late cancelled result, never the retained recording.
                if path != self.recording.path:
                    self.recording.remove(path)
