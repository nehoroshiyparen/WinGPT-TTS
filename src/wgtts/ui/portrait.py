"""A portrait and its frame form one portable, serializable appearance."""

import re
from dataclasses import asdict, dataclass, replace
from functools import lru_cache

from PIL import Image, ImageColor, ImageDraw, ImageSequence

from wgtts.paths import image_path, portable_image_path

DEFAULT_IMAGE = "assets/arthas.gif"


@dataclass(frozen=True)
class Border:
    style: str = "bevel"
    width: int = 9
    color: str = "#92999c"

    def __post_init__(self):
        if self.style not in {"bevel", "flat", "none"}:
            raise ValueError("Стиль рамки: bevel, flat или none.")
        if type(self.width) is not int or not 0 <= self.width <= 30:
            raise ValueError("Толщина рамки: целое число от 0 до 30.")
        if not isinstance(self.color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color):
            raise ValueError("Цвет рамки: #RRGGBB, например #92999c.")


@dataclass(frozen=True)
class Appearance:
    image: str = DEFAULT_IMAGE
    border: Border = Border()

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Оформление должно содержать image и border.")
        image = value.get("image", DEFAULT_IMAGE)
        if not isinstance(image, str) or not image.strip():
            raise ValueError("Укажи путь к GIF или изображению.")
        border = value.get("border", {})
        if not isinstance(border, dict) or set(border) - {"style", "width", "color"}:
            raise ValueError("Параметры рамки: style, width, color.")
        return cls(image, Border(**border))

    def to_dict(self):
        value = asdict(self)
        value["image"] = portable_image_path(self.image)
        return value

    @property
    def path(self):
        return image_path(self.image)

    def with_option(self, name, raw):
        if name in {"image", "gif"}:
            return replace(self, image=raw)
        field = {"border-width": "width", "border-color": "color", "border-style": "style"}[name]
        value = int(raw) if field == "width" else raw
        changes = {field: value}
        if field == "width" and value > 0 and self.border.style == "none":
            changes["style"] = "bevel"
        return replace(self, border=replace(self.border, **changes))


# Each preset carries both the image and the border, including user-saved ones.
PRESETS = {
    "classic": ("Классическая сталь", Appearance()),
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


class Portrait:
    """Owns source animation, geometry and compositing for one appearance."""

    def __init__(self, appearance):
        self.appearance = appearance
        self.frames, self.durations = [], []
        with Image.open(appearance.path) as source:
            for frame in ImageSequence.Iterator(source):
                self.frames.append(frame.convert("RGBA"))
                self.durations.append(max(20, frame.info.get("duration", 100)))
        if not self.frames:
            raise ValueError("В изображении нет кадров.")
        self.native_size = self.frames[0].size

    def content_box(self, size):
        border = self.appearance.border
        inset = round(border.width * size / 100) if border.style != "none" else 0
        width, height = (max(1, round(length * size / 100)) for length in self.native_size)
        return inset, inset, inset + width, inset + height

    def canvas_size(self, size):
        left, top, right, bottom = self.content_box(size)
        return right + left, bottom + top

    def render(self, index, size):
        left, top, right, bottom = self.content_box(size)
        width, height = self.canvas_size(size)
        frame = self.frames[index]
        if frame.size != (right - left, bottom - top):
            frame = frame.resize((right - left, bottom - top), Image.Resampling.LANCZOS)
        result = Image.new("RGBA", (width, height), "#11151d")
        result.alpha_composite(frame, (left, top))
        result.alpha_composite(_frame_overlay(width, height, left, self.appearance.border))
        return result
