"""Tk owns the main thread; speech and terminal never call Tk methods."""

import sys
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, simpledialog

from PIL import Image, ImageDraw, ImageTk

from wgtts.branding import APP_ID, APP_NAME
from wgtts.paths import ASSETS_DIR
from wgtts.ui.portrait import Appearance, Portrait


def _windows_app_identity():
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def _windows_taskbar(root):
    """Keep a borderless Tk window in Alt+Tab and give it its own taskbar icon."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongW.restype = wintypes.LONG
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    handle = user32.GetParent(root.winfo_id()) or root.winfo_id()
    style = user32.GetWindowLongW(handle, -20)
    user32.SetWindowLongW(handle, -20, (style | 0x00040000) & ~0x00000080)
    user32.SetWindowPos(handle, None, 0, 0, 0, 0, 0x0020 | 0x0010 | 0x0004 | 0x0002 | 0x0001)


def _control_image(path, master):
    # Compose the supplied transparent glyph on a small, antialiased disc.
    scale, size = 4, 30
    disc = Image.new("RGBA", (size * scale, size * scale))
    ImageDraw.Draw(disc).ellipse(
        (0, 0, size * scale - 1, size * scale - 1), fill=(255, 255, 255, 235)
    )
    with Image.open(path) as source:
        glyph = source.convert("RGBA")
        glyph.thumbnail((14 * scale, 14 * scale), Image.Resampling.LANCZOS)
        disc.alpha_composite(
            glyph, ((disc.width - glyph.width) // 2, (disc.height - glyph.height) // 2)
        )
    return ImageTk.PhotoImage(disc.resize((size, size), Image.Resampling.LANCZOS), master=master)


class SpeechWidget:
    def __init__(
        self,
        gif,
        speech,
        on_start,
        on_stop,
        on_close,
        *,
        root=None,
        size=100,
        on_resize=None,
        size_provider=None,
        appearance=None,
        appearance_provider=None,
        presets_provider=None,
        on_preset=None,
        on_option=None,
        on_save_preset=None,
    ):
        _windows_app_identity()
        self.root = root or tk.Tk()
        self.root.withdraw()
        self.speech = speech
        self.on_start, self.on_stop = on_start, on_stop
        self.on_resize = on_resize
        self.size_provider = size_provider
        self.appearance_provider = appearance_provider
        self.presets_provider, self.on_preset = presets_provider, on_preset
        self.on_option, self.on_save_preset = on_option, on_save_preset
        self.on_close = on_close
        self.size = max(50, min(300, int(size)))
        self.wheel_delta = 0
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#11151d", bd=0)
        self.root.protocol("WM_DELETE_WINDOW", on_close)
        self.portrait = Portrait(appearance or Appearance(image=str(gif)))
        self.durations = self.portrait.durations
        self.width, self.height = self.portrait.canvas_size(self.size)
        self.frames = [None] * len(self.portrait.frames)
        self._update_app_icon()
        self.displayed_frame = 0
        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bd=0,
            highlightthickness=0,
            bg="#11151d",
        )
        self.canvas.pack()
        self.picture = self.canvas.create_image(0, 0, image=self._frame(0), anchor="nw")
        self.icons = {
            name: _control_image(ASSETS_DIR / (name + ".png"), self.root)
            for name in ("play", "pause")
        }
        self.control_x, self.control_y = self._control_position()
        self.control = self.canvas.create_image(
            self.control_x, self.control_y, image=self.icons["play"], state="hidden"
        )
        self.hovering = False
        self.drag_origin = None
        self.dragging = False
        self.pressed_control = False
        self.canvas.bind("<Enter>", self._enter)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.root.bind("<Alt-F4>", lambda event: on_close())
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="Закрыть", command=on_close)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.index = 0
        self.next_frame = 0
        self.last_state = None
        self.root.update_idletasks()
        _windows_taskbar(self.root)
        self.root.deiconify()
        self.root.after(30, self._tick)

    def _frame(self, index):
        if self.frames[index] is None:
            self.frames[index] = ImageTk.PhotoImage(
                self.portrait.render(index, self.size), master=self.root
            )
        return self.frames[index]

    def _update_app_icon(self):
        # The taskbar uses the same portrait and frame as the widget.
        image = self.portrait.render(0, 100)
        image.thumbnail((128, 128), Image.Resampling.LANCZOS)
        self.app_icon = ImageTk.PhotoImage(image, master=self.root)
        self.root.iconphoto(True, self.app_icon)

    def _control_position(self):
        _, _, right, bottom = self.portrait.content_box(self.size)
        return right - 23, bottom - 23

    def set_appearance(self, appearance):
        if self.portrait.appearance == appearance:
            return
        self.portrait = Portrait(appearance)
        self.durations = self.portrait.durations
        self.index = self.displayed_frame = 0
        self.next_frame = 0
        self._update_app_icon()
        self._refresh_geometry()

    def resize(self, size):
        size = max(50, min(300, int(size)))
        if self.size == size:
            return
        self.size = size
        self._refresh_geometry()

    def _refresh_geometry(self):
        self.width, self.height = self.portrait.canvas_size(self.size)
        # Resize original pixels on demand; repeated zooming does not accumulate
        # blur or create all 83 Tk images on every wheel event.
        self.frames = [None] * len(self.portrait.frames)
        self.canvas.configure(width=self.width, height=self.height)
        self.canvas.itemconfigure(self.picture, image=self._frame(self.displayed_frame))
        self.control_x, self.control_y = self._control_position()
        self.canvas.coords(self.control, self.control_x, self.control_y)
        self.root.geometry(f"{self.width}x{self.height}")

    def _wheel(self, event):
        self.wheel_delta += event.delta
        steps = int(self.wheel_delta / 120)
        if steps:
            self.wheel_delta -= steps * 120
            previous = self.size
            self.resize(self.size + steps * 10)
            if self.on_resize and self.size != previous:
                self.on_resize(self.size)
        return "break"

    def _show_menu(self, event):
        self.menu.delete(0, "end")
        for submenu in self.menu.winfo_children():
            submenu.destroy()
        if self.presets_provider and self.on_preset:
            appearances = tk.Menu(self.menu, tearoff=False)
            current = next(
                (
                    name
                    for name, (_, appearance) in self.presets_provider().items()
                    if appearance == self.portrait.appearance
                ),
                "",
            )
            self.preset_choice = tk.StringVar(master=self.root, value=current)
            for name, (label, appearance) in self.presets_provider().items():
                appearances.add_radiobutton(
                    label=label,
                    value=name,
                    command=lambda key=name: self.on_preset(key),
                    variable=self.preset_choice,
                )
            self.menu.add_cascade(label="Оформление", menu=appearances)
        if self.on_option:
            self.menu.add_command(label="Выбрать портрет…", command=self._choose_image)
            self.menu.add_command(label="Цвет рамки…", command=self._choose_color)
            widths = tk.Menu(self.menu, tearoff=False)
            self.border_width_choice = tk.IntVar(
                master=self.root, value=self.portrait.appearance.border.width
            )
            for width in (0, 2, 6, 9, 14, 20):
                widths.add_radiobutton(
                    label=str(width),
                    value=width,
                    variable=self.border_width_choice,
                    command=lambda value=width: self.on_option("border-width", str(value)),
                )
            self.menu.add_cascade(label="Толщина рамки", menu=widths)
        if self.on_save_preset:
            self.menu.add_command(label="Сохранить оформление…", command=self._save_preset)
        if self.on_preset or self.on_option or self.on_save_preset:
            self.menu.add_separator()
        self.menu.add_command(label="Закрыть", command=self.on_close)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _choose_image(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title=f"Портрет {APP_NAME}",
            filetypes=[
                ("Изображения", "*.gif *.png *.jpg *.jpeg *.webp *.bmp"),
                ("Все файлы", "*.*"),
            ],
        )
        if path:
            self.on_option("image", path)

    def _choose_color(self):
        _, color = colorchooser.askcolor(
            self.portrait.appearance.border.color, parent=self.root, title="Цвет рамки"
        )
        if color:
            self.on_option("border-color", color)

    def _save_preset(self):
        name = simpledialog.askstring("Сохранить оформление", "Название пресета:", parent=self.root)
        if name:
            self.on_save_preset(name)

    def _enter(self, event):
        self.hovering = True
        self._update_control()

    def _leave(self, event):
        self.hovering = False
        self._update_control()

    def _over_control(self, event):
        return (event.x - self.control_x) ** 2 + (event.y - self.control_y) ** 2 <= 15**2

    def _motion(self, event):
        self.canvas.configure(cursor="hand2" if self._over_control(event) else "fleur")

    def _press(self, event):
        self.pressed_control = self.hovering and self._over_control(event)
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())
        self.dragging = False

    def _drag(self, event):
        if self.drag_origin is None or self.pressed_control:
            return
        pointer_x, pointer_y, window_x, window_y = self.drag_origin
        dx, dy = event.x_root - pointer_x, event.y_root - pointer_y
        if not self.dragging and abs(dx) + abs(dy) < 4:
            return
        self.dragging = True
        x, y = window_x + dx, window_y + dy
        if sys.platform == "win32":
            # Native coordinates also work on monitors left of the main screen.
            import ctypes

            user32 = ctypes.windll.user32
            handle = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            user32.SetWindowPos(handle, None, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)
        else:
            self.root.geometry(f"{x:+d}{y:+d}")
        self._update_control()

    def _release(self, event):
        activate = self.pressed_control and self._over_control(event)
        self.drag_origin = None
        self.pressed_control = False
        self.dragging = False
        if activate:
            if self.speech.state in {"preparing", "playing"}:
                self.on_stop()
            else:
                self.on_start()
        self._update_control()

    def _update_control(self):
        name = "pause" if self.speech.state in {"preparing", "playing"} else "play"
        self.canvas.itemconfigure(
            self.control,
            image=self.icons[name],
            state="normal" if self.hovering and not self.dragging else "hidden",
        )

    def _tick(self):
        if self.appearance_provider is not None:
            self.set_appearance(self.appearance_provider())
        if self.size_provider is not None:
            self.resize(self.size_provider())
        state = self.speech.state
        now = time.monotonic()
        if state != self.last_state:
            self.root.title(
                APP_NAME
                + {
                    "preparing": " · готовлю голос",
                    "playing": " · говорит",
                    "error": " · ошибка, см. терминал",
                }.get(state, "")
            )
            self._update_control()
            self.last_state = state
        if state == "playing":
            if now >= self.next_frame:
                self.canvas.itemconfigure(self.picture, image=self._frame(self.index))
                self.displayed_frame = self.index
                self.next_frame = now + self.durations[self.index] / 1000
                self.index = (self.index + 1) % len(self.frames)
        else:
            self.index = 0
            self.next_frame = 0
            if self.displayed_frame != 0:
                self.canvas.itemconfigure(self.picture, image=self._frame(0))
                self.displayed_frame = 0
        self.root.after(20, self._tick)
