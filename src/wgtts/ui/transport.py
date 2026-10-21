"""Scalable playback controls with one geometry for drawing and hit testing."""

from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageTk

from wgtts.paths import ASSETS_DIR
from wgtts.speech.playback import format_time

MAX_PLAYER_SCALE = 1.0
PANEL_BACKGROUND = "#181c23"
ACCENT = "#9dccf1"


@dataclass
class DrawerMotion:
    value: float = 0
    target: float = 0
    origin: float = 0
    started: float = 0
    duration: float = 0.24

    def at(self, now):
        progress = max(0, min(1, (now - self.started) / self.duration))
        self.value = self.origin + (self.target - self.origin) * (1 - (1 - progress) ** 3)
        return self.value

    def to(self, target, now):
        if target != self.target:
            self.origin = self.at(now)
            self.target, self.started = target, now


@dataclass(frozen=True)
class TransportLayout:
    x: float
    y: float
    width: int
    height: int
    scale: float

    @classmethod
    def for_portrait(cls, box):
        left, top, right, bottom = box
        width = max(1, round(right - left))
        scale = min(MAX_PLAYER_SCALE, max(0.75, width / 231))
        return cls(0, 0, width, round(70 * scale), scale)

    @property
    def centers(self):
        center, y = self.x + self.width / 2, self.y + 23 * self.scale
        result = {
            "back": (center - 36 * self.scale, y),
            "play": (center, y),
            "forward": (center + 36 * self.scale, y),
        }
        return result if self.width >= 100 else {"play": result["play"]}

    def diameter(self, action):
        return round((36 if action == "play" else 28) * self.scale)

    @property
    def track(self):
        return (
            self.x + 12 * self.scale,
            self.x + self.width - 12 * self.scale,
            self.y + 47 * self.scale,
        )

    def hit(self, x, y):
        # Disabled controls still own their area: they must never drag the window.
        for action, (cx, cy) in self.centers.items():
            radius = self.diameter(action) / 2 + 4 * self.scale
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
                return action
        left, right, track_y = self.track
        if (
            left - 5 * self.scale <= x <= right + 5 * self.scale
            and abs(y - track_y) <= 10 * self.scale
        ):
            return "seek"
        if self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height:
            return "panel"
        return None

    def position(self, x, duration):
        left, right, _ = self.track
        return max(0, min(1, (x - left) / (right - left))) * duration


@lru_cache(maxsize=128)
def _button_art(action, diameter, style):
    factor = 4
    size = diameter * factor
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    primary = action in {"play", "pause"}
    background = ACCENT if primary else PANEL_BACKGROUND
    foreground = "#122334" if primary else "#dce2eb"
    if style == "hover":
        background = "#b6ddfa" if primary else "#2b313b"
    elif style == "pressed":
        background = "#7bb5e0" if primary else "#222831"
    elif style == "disabled":
        background, foreground = PANEL_BACKGROUND, "#58616f"
    draw.ellipse((2, 2, size - 3, size - 3), fill=background)
    if primary:
        with Image.open(ASSETS_DIR / (action + ".png")) as source:
            glyph = source.convert("RGBA")
        glyph.thumbnail((round(size * 0.38), round(size * 0.38)), Image.Resampling.LANCZOS)
        ink = Image.new("RGBA", glyph.size, foreground)
        ink.putalpha(glyph.getchannel("A"))
        image.alpha_composite(ink, ((size - ink.width) // 2, (size - ink.height) // 2))
    else:
        # A circular arrow and a legible 10 replace the old loose +/- labels.
        inset = round(size * 0.2)
        draw.arc(
            (inset, inset, size - inset, size - inset),
            215,
            500,
            fill=foreground,
            width=max(3, round(size * 0.055)),
        )
        draw.polygon(
            [
                (inset - 2, size * 0.29),
                (inset + size * 0.22, size * 0.29),
                (inset + size * 0.08, size * 0.11),
            ],
            fill=foreground,
        )
        if action == "forward":
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("segoeuib.ttf", round(size * 0.31))
        except OSError:
            font = ImageFont.load_default(size=round(size * 0.31))
        draw.text((size / 2, size * 0.52), "10", fill=foreground, font=font, anchor="mm")
    return image.resize((diameter, diameter), Image.Resampling.LANCZOS)


class TransportPanel:
    def __init__(self, canvas, layout, name=""):
        self.canvas, self.layout = canvas, layout
        self.images = {}
        self.offset_y = 0
        options = {"tags": "transport", "state": "hidden"}
        self.plate = canvas.create_rectangle(
            0, 0, layout.width, layout.height, fill=PANEL_BACKGROUND, outline="", **options
        )
        self.buttons = {
            action: canvas.create_image(x, y, **options)
            for action, (x, y) in layout.centers.items()
        }
        left, right, y = layout.track
        self.track = canvas.create_line(
            left, y, right, y, fill="#39414c", width=2, capstyle="round", **options
        )
        self.fill = canvas.create_line(
            left, y, left, y, fill=ACCENT, width=2, capstyle="round", **options
        )
        self.knob = canvas.create_oval(0, 0, 1, 1, fill="#e5f5ff", outline="", **options)
        font = ("Segoe UI", -max(10, round(11 * layout.scale)))
        self.elapsed = canvas.create_text(
            left, y + 13 * layout.scale, anchor="w", fill="#a7afb9", font=font, **options
        )
        self.total = canvas.create_text(
            right, y + 13 * layout.scale, anchor="e", fill="#a7afb9", font=font, **options
        )
        row_y = layout.centers["play"][1]
        self.name = canvas.create_text(
            left, row_y, text=name, anchor="w", fill="#eef1f6", font=font, **options
        )
        self.status = canvas.create_text(
            right, row_y, anchor="e", fill="#a7afb9", font=font, **options
        )

    def move_to(self, y):
        self.canvas.move("transport", 0, y - self.offset_y)
        self.offset_y = y

    def _label(self, item, text, width):
        self.canvas.itemconfigure(item, text=text)
        while text and width > 0:
            box = self.canvas.bbox(item)
            if box is None or box[2] - box[0] <= width:
                break
            text = text[:-2] + "…" if len(text) > 2 else ""
            self.canvas.itemconfigure(item, text=text)

    def destroy(self):
        self.canvas.delete("transport")
        self.images.clear()

    @staticmethod
    def enabled(action, duration, state, can_seek):
        return action == "play" or (
            action in {"back", "forward", "seek"}
            and can_seek
            and duration > 0
            and state != "preparing"
        )

    def update(
        self,
        position,
        duration,
        state,
        *,
        visible,
        hovered=None,
        pressed=None,
        can_seek=True,
        name="",
    ):
        canvas, layout = self.canvas, self.layout
        canvas.itemconfigure("transport", state="normal" if visible else "hidden")
        if not visible:
            return
        for action, item in self.buttons.items():
            glyph = "pause" if action == "play" and state in {"playing", "preparing"} else action
            style = (
                "disabled"
                if not self.enabled(action, duration, state, can_seek)
                else "pressed"
                if pressed == action
                else "hover"
                if hovered == action
                else "normal"
            )
            key = glyph, layout.diameter(action), style
            if key not in self.images:
                self.images[key] = ImageTk.PhotoImage(_button_art(*key), master=canvas)
            canvas.itemconfigure(item, image=self.images[key])
        left, right, y = layout.track
        y += self.offset_y
        fraction = min(1, max(0, position / duration)) if duration else 0
        x = left + fraction * (right - left)
        radius = 4 if hovered == "seek" or pressed == "seek" else 3
        canvas.coords(self.fill, left, y, x, y)
        canvas.coords(self.knob, x - radius, y - radius, x + radius, y + radius)
        canvas.itemconfigure(self.elapsed, text=format_time(position) if duration else "Нет записи")
        canvas.itemconfigure(self.total, text=format_time(duration) if duration else "—:—")
        canvas.itemconfigure(self.total, state="normal" if layout.width >= 100 else "hidden")
        label_width = layout.width / 2 - 63 * layout.scale
        status = {
            "playing": "Говорит",
            "paused": "Пауза",
            "preparing": "Готовлю",
            "error": "Ошибка",
        }.get(state, "Готов")
        for item, text in ((self.name, name), (self.status, status)):
            canvas.itemconfigure(item, state="normal" if label_width >= 34 else "hidden")
            if label_width >= 34:
                self._label(item, text, label_width)
