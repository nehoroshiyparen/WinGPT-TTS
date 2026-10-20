"""Keep the last completed recording until a new synthesis replaces it."""

import hashlib
import json
import re
from pathlib import Path

AUDIO_NAME = re.compile(r"speech-[0-9a-f]{32}\.wav")


def recording_key(text, settings):
    payload = json.dumps(
        [text, settings["url"], settings["tts"]], ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecordingCache:
    def __init__(self, directory, notify):
        self.directory = Path(directory)
        self.manifest = self.directory / "last-recording.json"
        self.notify = notify
        self.path = None
        self.key = None
        try:
            saved = json.loads(self.manifest.read_text(encoding="utf-8"))
            name, key = saved["file"], saved["key"]
            if isinstance(name, str) and isinstance(key, str) and AUDIO_NAME.fullmatch(name):
                path = self.directory / name
                if self.owns(path) and path.is_file():
                    self.path, self.key = path, key
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def owns(self, path):
        path = Path(path)
        return (
            AUDIO_NAME.fullmatch(path.name)
            and not path.is_symlink()
            and path.resolve().parent == self.directory.resolve()
        )

    def find(self, key):
        if key == self.key and self.path is not None and self.path.is_file():
            return self.path
        return None

    def replace(self, path, key):
        previous = self.path
        self.path, self.key = Path(path), key
        try:
            if self.owns(self.path) and self.path.is_file():
                temporary = self.manifest.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps({"file": self.path.name, "key": key}), encoding="utf-8"
                )
                temporary.replace(self.manifest)
        except OSError as error:
            self.notify(f"Не удалось сохранить сведения о записи: {error}")
        if previous is not None and previous != self.path:
            self.remove(previous)

    def remove(self, path):
        if path is None:
            return
        try:
            if self.owns(path):
                Path(path).unlink(missing_ok=True)
        except OSError as error:
            self.notify(f"Не удалось убрать временную запись: {error}")
