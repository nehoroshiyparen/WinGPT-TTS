"""Scalable playback controls with one geometry for drawing and hit testing."""

from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageTk

from wgtts.paths import ASSETS_DIR
from wgtts.speech.playback import format_time

MAX_PLAYER_SCALE = 1.0


def dock_position(anchor, work_area):
    left, top, width, height, panel_width, panel_height = anchor
    screen_left, screen_top, screen_right, screen_bottom = work_area
    x = max(screen_left, min(screen_right - panel_width, left + (width - panel_width) // 2))
    y = top + height + 6
    if y + panel_height > screen_bottom:
        y = top - panel_height - 6
    return x, max(screen_top, min(screen_bottom - panel_height, y))


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
        width, height = right - left, bottom - top
        compact = width < 205 or height < 180
        scale = min(
            MAX_PLAYER_SCALE,
            max(0.7 if compact else 0.675, min(width / 231, height / 203) * 0.75),
        )
        panel_height = round(92 * scale)
        panel_width = round((231 if compact else 215) * scale)
        return cls(0, 0, panel_width, panel_height, scale)

    @property
    def centers(self):
        center, y = self.x + self.width / 2, self.y + 28 * self.scale
        return {
            "back": (center - 52 * self.scale, y),
            "play": (center, y),
            "forward": (center + 52 * self.scale, y),
        }

    def diameter(self, action):
        return round((40 if action == "play" else 30) * self.scale)

    @property
    def track(self):
        return (
            self.x + 16 * self.scale,
            self.x + self.width - 16 * self.scale,
            self.y + 59 * self.scale,
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
    background = "#e3f2fb" if primary else "#273647"
    foreground = "#152435" if primary else "#d4e5f2"
    outline = "#f3faff" if primary else "#4a6076"
    if style == "hover":
        background = "#ffffff" if primary else "#3a5067"
        outline = "#b5e0ff"
    elif style == "pressed":
        background = "#a6cde6" if primary else "#1c2b3b"
    elif style == "disabled":
        background, foreground, outline = "#1d2835", "#586879", "#344253"
    draw.ellipse((2, 2, size - 3, size - 3), fill=background, outline=outline, width=factor)
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


def _plate_art(width, height):
    factor = 3
    image = Image.new("RGBA", (width * factor, height * factor))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (1, 1, width * factor - 2, height * factor - 2),
        radius=round(15 * height / 92 * factor),
        fill="#111c29",
        outline="#455b71",
        width=factor,
    )
    return image.resize((width, height), Image.Resampling.LANCZOS)


class TransportPanel:
    def __init__(self, canvas, layout):
        self.canvas, self.layout = canvas, layout
        self.images = {}
        self.plate_image = ImageTk.PhotoImage(
            _plate_art(layout.width, layout.height), master=canvas
        )
        options = {"tags": "transport", "state": "hidden"}
        self.plate = canvas.create_image(
            layout.x, layout.y, image=self.plate_image, anchor="nw", **options
        )
        self.buttons = {
            action: canvas.create_image(x, y, **options)
            for action, (x, y) in layout.centers.items()
        }
        left, right, y = layout.track
        self.track = canvas.create_line(
            left, y, right, y, fill="#40556c", width=3 * layout.scale, capstyle="round", **options
        )
        self.fill = canvas.create_line(
            left, y, left, y, fill="#8fccef", width=3 * layout.scale, capstyle="round", **options
        )
        self.knob = canvas.create_oval(0, 0, 1, 1, fill="#e5f5ff", outline="", **options)
        font = ("Segoe UI", -max(10, round(10 * layout.scale)))
        self.elapsed = canvas.create_text(
            left, y + 18 * layout.scale, anchor="w", fill="#d7e6f2", font=font, **options
        )
        self.total = canvas.create_text(
            right, y + 18 * layout.scale, anchor="e", fill="#8fa6bc", font=font, **options
        )

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
        self, position, duration, state, *, visible, hovered=None, pressed=None, can_seek=True
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
        fraction = min(1, max(0, position / duration)) if duration else 0
        x = left + fraction * (right - left)
        radius = (4 if hovered == "seek" or pressed == "seek" else 3) * layout.scale
        canvas.coords(self.fill, left, y, x, y)
        canvas.coords(self.knob, x - radius, y - radius, x + radius, y + radius)
        canvas.itemconfigure(self.elapsed, text=format_time(position) if duration else "Нет записи")
        canvas.itemconfigure(self.total, text=format_time(duration) if duration else "—:—")
