"""Project settings; Applio's own settings are never modified."""

import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from wgtts.paths import DATA_DIR
from wgtts.ui.portrait import PRESETS, Appearance, Portrait


def local_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Адрес Applio должен быть локальным HTTP-адресом, например http://127.0.0.1:8000."
        )
    return value.rstrip("/")


class Settings:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else DATA_DIR / "settings.json"
        self.data = {
            "url": "http://127.0.0.1:8000",
            "delay": 0.3,
            "poll": 1.5,
            "stable": 2.5,
            "size": 100,
            "volume": 100,
            "portrait": Appearance().to_dict(),
            "portrait_presets": {},
            "tts": {},
        }
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            # Old installations stored the GIF separately; retain their portrait.
            if "portrait" not in loaded and loaded.get("gif"):
                loaded["portrait"] = Appearance(image=loaded["gif"]).to_dict()
            loaded.pop("gif", None)
            self.data.update(loaded)
        self.data["portrait"] = Appearance.from_dict(self.data["portrait"]).to_dict()
        local_url(self.data["url"])

    @property
    def appearance(self):
        return Appearance.from_dict(self.data["portrait"])

    def presets(self):
        result = {name: (label, appearance) for name, (label, appearance) in PRESETS.items()}
        for name, value in self.data["portrait_presets"].items():
            result[name] = (name, Appearance.from_dict(value))
        return result

    def apply_preset(self, name):
        presets = self.presets()
        if name not in presets:
            raise ValueError("Неизвестный пресет. Доступны: " + ", ".join(presets))
        appearance = presets[name][1]
        Portrait(appearance)  # Decode before saving, so a bad file cannot break the widget.
        self.data["portrait"] = appearance.to_dict()
        self.save()

    def save_preset(self, name):
        if not name.strip() or len(name) > 60:
            raise ValueError("Имя пресета: от 1 до 60 символов.")
        if name in PRESETS:
            raise ValueError("Выбери другое имя: встроенные пресеты сохраняются отдельно.")
        self.data["portrait_presets"][name] = self.appearance.to_dict()
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def set(self, name, raw):
        if name in {"image", "gif", "border-width", "border-color", "border-style"}:
            appearance = self.appearance.with_option(name, raw)
            if name in {"image", "gif"}:
                Portrait(appearance)
            self.data["portrait"] = appearance.to_dict()
            self.save()
            return raw
        ranges = {
            "delay": (0.1, 3),
            "poll": (0.5, 30),
            "stable": (1, 30),
            "size": (50, 300),
            "volume": (0, 100),
            "rate": (-100, 100),
            "pitch": (-24, 24),
        }
        aliases = {
            "rate": "ttsRate",
            "pitch": "pitch",
            "voice": "ttsVoice",
            "model": "pthPath",
            "index": "indexPath",
        }
        if name in ranges:
            value = float(raw)
            low, high = ranges[name]
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name}: допустимо от {low} до {high}.")
            if name in ("rate", "pitch", "size", "volume"):
                value = int(value)
        elif name == "url":
            value = local_url(raw)
        elif name in ("voice", "model", "index"):
            value = raw
        else:
            raise ValueError(
                "Настройки: volume, size, image, border-width, border-color, border-style, delay, poll, stable, rate, pitch, voice, model, index, url."
            )
        if name in aliases:
            self.data["tts"][aliases[name]] = value
        else:
            self.data[name] = value
        self.save()
        return value
