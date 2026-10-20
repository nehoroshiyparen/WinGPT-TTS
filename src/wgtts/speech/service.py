"""One cancellable synthesis/playback queue shared by the terminal and widget."""

from collections import deque
from copy import deepcopy
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
            self.condition.notify_all()

    def _stop_locked(self):
        self.epoch += 1
        self.pending.clear()
        self.cancel.set()
        self.player.stop()
        self.state = "idle"

    def stop(self):
        with self.condition:
            self.cancel_revision += 1
            self._stop_locked()

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
