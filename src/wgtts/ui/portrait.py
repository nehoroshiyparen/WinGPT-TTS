"""A portrait and its frame form one portable, serializable appearance."""

import re
from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from io import BytesIO
from itertools import accumulate
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageOps

from wgtts.paths import ASSETS_DIR, image_path, portable_image_path

DEFAULT_IMAGE = "assets/arthas-talk.webp"
DEFAULT_IDLE = "assets/arthas-idle.webp"


@dataclass(frozen=True)
class Border:
    style: str = "bevel"
    width: int = 9
    color: str = "#92999c"

    def __post_init__(self):
        if self.style not in {"card", "bevel", "flat", "none"}:
            raise ValueError("Стиль рамки: card, bevel, flat или none.")
        if type(self.width) is not int or not 0 <= self.width <= 30:
            raise ValueError("Толщина рамки: целое число от 0 до 30.")
        if not isinstance(self.color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color):
            raise ValueError("Цвет рамки: #RRGGBB, например #92999c.")


@dataclass(frozen=True)
class Appearance:
    # image remains the talking portrait for callers using the old single-file API.
    image: str | None = None
    border: Border = Border("card", 0, "#383f49")
    idle: str | None = None
    name: str = ""

    def __post_init__(self):
        if self.image is None:
            object.__setattr__(self, "image", DEFAULT_IMAGE)
            if self.idle is None:
                object.__setattr__(self, "idle", DEFAULT_IDLE)
        for path in (self.image, self.idle):
            if path is not None and (not isinstance(path, str) or not path.strip()):
                raise ValueError("Укажи путь к GIF, WebP или изображению.")
        if not isinstance(self.name, str) or len(self.name) > 60:
            raise ValueError("Имя персонажа: не более 60 символов.")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Оформление должно содержать animations и border.")
        border = value.get("border", asdict(cls().border))
        if not isinstance(border, dict) or set(border) - {"style", "width", "color"}:
            raise ValueError("Параметры рамки: style, width, color.")
        if "animations" in value:
            animations = value["animations"]
            if (
                not isinstance(animations, dict)
                or set(animations) - {"idle", "talk"}
                or not isinstance(animations.get("talk"), str)
            ):
                raise ValueError("Анимации персонажа: idle (ожидание) и talk (разговор).")
            return cls(
                animations["talk"], Border(**border), animations.get("idle"), value.get("name", "")
            )
        if "image" not in value:
            return cls(border=Border(**border))
        image = value["image"]
        if not isinstance(image, str) or not image.strip():
            raise ValueError("Укажи путь к GIF, WebP или изображению.")
        # Upgrade only the old bundled Arthas; keep custom single-file portraits intact.
        if image_path(image).resolve() == ASSETS_DIR / "arthas.gif":
            return cls(border=Border(**border))
        return cls(image, Border(**border))

    def to_dict(self):
        return {
            "name": self.name,
            "animations": {
                "idle": portable_image_path(self.idle) if self.idle else None,
                "talk": portable_image_path(self.image),
            },
            "border": asdict(self.border),
        }

    @property
    def path(self):
        return image_path(self.image)

    @property
    def character_name(self):
        if self.name.strip():
            return self.name.strip()
        if self.path == ASSETS_DIR / "arthas-talk.webp":
            return "Артас"
        return (
            re.sub(r"[-_ ](?:talk|idle|afk|portrait)$", "", Path(self.image).stem, flags=re.I)
            or "Персонаж"
        )

    def with_option(self, name, raw):
        if name in {"image", "gif"}:
            return replace(self, image=raw, idle=None)
        if name == "talk":
            return replace(self, image=raw)
        if name == "idle":
            return replace(self, idle=None if raw == "none" else raw)
        if name == "character-name":
            return replace(self, name=raw.strip())
        field = {"border-width": "width", "border-color": "color", "border-style": "style"}[name]
        value = int(raw) if field == "width" else raw
        changes = {field: value}
        if field == "width" and value > 0 and self.border.style in {"none", "card"}:
            changes["style"] = "bevel"
        return replace(self, border=replace(self.border, **changes))


# Each preset carries both the image and the border, including user-saved ones.
PRESETS = {
    "modern": ("Современная карточка", Appearance()),
    "classic": ("Классическая сталь", Appearance(border=Border())),
    "frost": ("Ледяная сталь", Appearance(border=Border(color="#7196ad"))),
    "minimal": ("Тонкая рамка", Appearance(border=Border("flat", 2, "#67747c"))),
    "bare": ("Без рамки", Appearance(border=Border("none", 0))),
}


@lru_cache(maxsize=16)
def _frame_overlay(width, height, thickness, border):
    """Mitered metal, lit from the upper left, with a recessed black inner edge."""
    overlay = Image.new("RGBA", (width, height))
    if not thickness:
        return overlay
    draw = ImageDraw.Draw(overlay)
    if border.style == "flat":
        draw.rectangle((0, 0, width - 1, height - 1), fill=border.color)
        draw.rectangle(
            (thickness, thickness, width - thickness - 1, height - thickness - 1), fill=(0, 0, 0, 0)
        )
        return overlay

    base = ImageColor.getrgb(border.color)
    # Cross-section: dark outer lip, bright bevel, brushed face, inner shadow.
    profile = (
        (0, 0.24),
        (0.12, 1.32),
        (0.23, 1.02),
        (0.58, 0.80),
        (0.72, 0.34),
        (0.86, 0.12),
        (1, 0.06),
    )

    def shade(level):
        return tuple(max(0, min(255, round(channel * level))) for channel in base) + (255,)

    for depth in range(thickness):
        position = depth / max(1, thickness - 1)
        for (start, light), (end, next_light) in zip(profile, profile[1:]):
            if start <= position <= end:
                level = light + (next_light - light) * (position - start) / (end - start)
                break
        right, bottom = width - 1 - depth, height - 1 - depth
        draw.line((depth, bottom, depth, depth, right, depth), fill=shade(level * 1.13))
        draw.line((right, depth + 1, right, bottom, depth + 1, bottom), fill=shade(level * 0.68))
        # Stable, subtle grain along the broad face; never changes across GIF frames.
        if 0.23 <= position <= 0.58:
            for x in range(depth + 1, right):
                grain = ((x * 17 + depth * 31) % 11 - 5) / 160
                draw.point((x, depth), fill=shade(level * 1.13 + grain))
                draw.point((x, bottom), fill=shade(level * 0.68 + grain))
            for y in range(depth + 1, bottom):
                grain = ((y * 17 + depth * 31) % 11 - 5) / 160
                draw.point((depth, y), fill=shade(level * 1.13 + grain))
                draw.point((right, y), fill=shade(level * 0.68 + grain))
    # Thin seams separate the four mitered pieces, as on a game portrait frame.
    seam = shade(0.45)
    for x, y, dx, dy in (
        (0, 0, 1, 1),
        (width - 1, 0, -1, 1),
        (0, height - 1, 1, -1),
        (width - 1, height - 1, -1, -1),
    ):
        draw.line((x, y, x + dx * (thickness - 1), y + dy * (thickness - 1)), fill=seam)
    return overlay


class Animation:
    """Keep compressed bytes and one decoder, rather than every decoded frame."""

    def __init__(self, path):
        # Close the file immediately so users can move/replace their source files.
        self.source = Image.open(BytesIO(path.read_bytes()))
        try:
            self.size = self.source.size
            self.durations = []
            for index in range(getattr(self.source, "n_frames", 1)):
                self.source.seek(index)
                self.source.load()  # WebP exposes duration only after decoding.
                self.durations.append(max(20, self.source.info.get("duration", 100)))
            self.ends = list(accumulate(self.durations))
            self.source.seek(0)
        except Exception:
            self.close()
            raise

    def __len__(self):
        return len(self.durations)

    def __getitem__(self, index):
        if not 0 <= index < len(self):
            raise IndexError(index)
        self.source.seek(index)
        return self.source.convert("RGBA")

    def frame_at(self, elapsed):
        return bisect_right(self.ends, max(0, elapsed) * 1000 % self.ends[-1])

    def close(self):
        self.source.close()


class Portrait:
    """Independent character animations share one stable canvas and border."""

    def __init__(self, appearance):
        self.appearance = appearance
        self.animations = {}
        try:
            self.animations["talk"] = Animation(appearance.path)
            if appearance.idle:
                idle_path = image_path(appearance.idle)
                self.animations["idle"] = (
                    self.animations["talk"]
                    if idle_path == appearance.path
                    else Animation(idle_path)
                )
        except Exception:
            self.close()
            raise
        self.frames = self.animations["talk"]
        self.durations = self.frames.durations
        self.native_size = self.frames.size
        # Higher quality assets must not suddenly make the desktop widget enormous.
        ratio = min(1, 231 / max(self.native_size))
        self.base_size = tuple(max(1, round(length * ratio)) for length in self.native_size)

    def close(self):
        for animation in set(self.animations.values()):
            animation.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def frame_at(self, elapsed, state="talk"):
        if state == "idle" and "idle" not in self.animations:
            return 0
        return self.animations[state].frame_at(elapsed)

    def content_box(self, size):
        border = self.appearance.border
        inset = round(border.width * size / 100) if border.style not in {"none", "card"} else 0
        width, height = (max(1, round(length * size / 100)) for length in self.base_size)
        return inset, inset, inset + width, inset + height

    def canvas_size(self, size):
        left, top, right, bottom = self.content_box(size)
        return right + left, bottom + top

    def render(self, index, size, state="talk"):
        left, top, right, bottom = self.content_box(size)
        width, height = self.canvas_size(size)
        animation = self.animations.get(state, self.frames)
        frame = animation[index]
        frame = ImageOps.contain(frame, (right - left, bottom - top), Image.Resampling.LANCZOS)
        result = Image.new("RGBA", (width, height), "#11151d")
        result.alpha_composite(
            frame,
            (left + (right - left - frame.width) // 2, top + (bottom - top - frame.height) // 2),
        )
        result.alpha_composite(_frame_overlay(width, height, left, self.appearance.border))
        return result
