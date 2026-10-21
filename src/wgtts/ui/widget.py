"""Tk owns the main thread; speech and terminal never call Tk methods."""

import sys
import time
import tkinter as tk
from collections import OrderedDict
from tkinter import colorchooser, filedialog, simpledialog

from PIL import Image, ImageTk

from wgtts.branding import APP_ID, APP_NAME
from wgtts.speech.playback import format_time
from wgtts.ui.portrait import Appearance, Portrait
from wgtts.ui.transport import TransportLayout, TransportPanel, dock_position


def _windows_app_identity():
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def _windows_taskbar(root, *, secondary=False):
    """Give the portrait a taskbar icon; keep its secondary panel unfocused."""
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
    style = (
        ((style | 0x08000080) & ~0x00040000) if secondary else ((style | 0x00040000) & ~0x00000080)
    )
    user32.SetWindowLongW(handle, -20, style)
    user32.SetWindowPos(handle, None, 0, 0, 0, 0, 0x0020 | 0x0010 | 0x0004 | 0x0002 | 0x0001)


def _work_area(root):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("monitor", wintypes.RECT),
                ("work", wintypes.RECT),
                ("flags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        monitor = user32.MonitorFromWindow(root.winfo_id(), 2)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.work.left, info.work.top, info.work.right, info.work.bottom
    left, top = root.winfo_vrootx(), root.winfo_vrooty()
    return left, top, left + root.winfo_vrootwidth(), top + root.winfo_vrootheight()


class SpeechWidget:
    FRAME_CACHE_SIZE = 8

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
        on_seek=None,
        on_restart=None,
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
        self.on_seek, self.on_restart = on_seek, on_restart
        self.size = max(50, min(300, int(size)))
        self.wheel_delta = 0
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#11151d", bd=0)
        self.root.protocol("WM_DELETE_WINDOW", on_close)
        self.portrait = Portrait(appearance or Appearance(image=str(gif)))
        self.width, self.height = self.portrait.canvas_size(self.size)
        self.frames = OrderedDict()
        self.animation_state = "talk" if speech.state == "playing" else "idle"
        self.animation_started = time.monotonic()
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
        self.hovering = False
        self.hide_job = None
        self.hovered_action = None
        self.drag_origin = None
        self.dragging = False
        self.pressed_action = None
        self.scrubbing = False
        self.seek_preview = None
        self.seek_duration = 0
        # On Windows, creating a transient while its owner is withdrawn can leave
        # it permanently unmapped (1x1), even after repeated deiconify calls.
        # Finish mapping the portrait before creating its playback panel.
        _windows_taskbar(self.root)
        self.root.deiconify()
        self.root.update_idletasks()
        self.dock = tk.Toplevel(self.root)
        self.dock.withdraw()
        self.dock.title(APP_NAME + " · управление")
        self.dock.overrideredirect(True)
        self.dock.attributes("-topmost", True)
        self.dock.transient(self.root)
        self.dock.configure(bg="#11151d")
        if sys.platform == "win32":
            self.dock.attributes("-toolwindow", True)
        self.dock_canvas = tk.Canvas(self.dock, bd=0, highlightthickness=0, bg="#11151d")
        self.dock_canvas.pack()
        self.panel = None
        self.dock_anchor = None
        self._layout_transport()
        for surface in (self.canvas, self.dock_canvas):
            surface.bind("<Enter>", self._enter)
            surface.bind("<Leave>", self._leave)
            surface.bind("<Motion>", self._motion)
            surface.bind("<ButtonPress-1>", self._press)
            surface.bind("<B1-Motion>", self._drag)
            surface.bind("<ButtonRelease-1>", self._release)
            surface.bind("<MouseWheel>", self._wheel)
            surface.bind("<Button-3>", self._show_menu)
        self.root.bind("<Configure>", self._root_configured)
        self.root.bind("<Destroy>", self._destroy)
        self.root.bind("<Alt-F4>", lambda event: on_close())
        self.dock.bind("<Alt-F4>", lambda event: on_close())
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="Закрыть", command=on_close)
        self.index = 0
        self.last_state = None
        self.root.update_idletasks()
        _windows_taskbar(self.dock, secondary=True)
        self.root.after(30, self._tick)

    def _frame(self, index):
        key = self.animation_state, index
        if key not in self.frames:
            self.frames[key] = ImageTk.PhotoImage(
                self.portrait.render(index, self.size, self.animation_state), master=self.root
            )
        self.frames.move_to_end(key)
        while len(self.frames) > self.FRAME_CACHE_SIZE:
            self.frames.popitem(last=False)
        return self.frames[key]

    def _destroy(self, event):
        if event.widget is self.root:
            self.portrait.close()

    def _update_app_icon(self):
        # The taskbar uses the same portrait and frame as the widget.
        image = self.portrait.render(0, 100)
        image.thumbnail((128, 128), Image.Resampling.LANCZOS)
        self.app_icon = ImageTk.PhotoImage(image, master=self.root)
        self.root.iconphoto(True, self.app_icon)

    def _layout_transport(self):
        if self.panel is not None:
            self.panel.destroy()
        layout = TransportLayout.for_portrait(self.portrait.content_box(self.size))
        self.transport_canvas = self.dock_canvas
        self.panel = TransportPanel(self.transport_canvas, layout)
        self.dock_canvas.configure(width=layout.width, height=layout.height)
        self.dock_anchor = None

    def _progress(self):
        return self.speech.progress if self.on_seek is not None else (0, 0)

    def _root_configured(self, event):
        if event.widget == self.root and self.hovering:
            self._position_dock()

    def _position_dock(self):
        anchor = (
            self.root.winfo_rootx(),
            self.root.winfo_rooty(),
            self.width,
            self.height,
            self.panel.layout.width,
            self.panel.layout.height,
        )
        if anchor == self.dock_anchor:
            return
        panel_width, panel_height = anchor[-2:]
        x, y = dock_position(anchor, _work_area(self.root))
        self.dock_anchor = anchor
        self.dock.geometry(f"{panel_width}x{panel_height}{x:+d}{y:+d}")
        self.dock.update_idletasks()
        if sys.platform == "win32":
            import ctypes

            # Native positioning also supports monitors with negative coordinates.
            user32 = ctypes.windll.user32
            handle = user32.GetParent(self.dock.winfo_id()) or self.dock.winfo_id()
            user32.SetWindowPos(handle, None, x, y, panel_width, panel_height, 0x0010 | 0x0004)

    def _update_transport(self):
        visible = (self.hovering or self.pressed_action is not None) and not self.dragging
        position, duration = self._progress()
        if self.seek_preview is not None:
            position = self.seek_preview
        self.panel.update(
            position,
            duration,
            self.speech.state,
            visible=visible,
            hovered=self.hovered_action,
            pressed=self.pressed_action,
            can_seek=self.on_seek is not None,
        )
        if visible:
            if not self.dock.winfo_ismapped():
                self.dock.deiconify()
                self.dock.update_idletasks()
                self.dock_anchor = None
            self._position_dock()
        else:
            self.dock.withdraw()

    def _transport_action(self, event):
        if event.widget is not self.transport_canvas:
            return None
        return self.panel.layout.hit(event.x, event.y)

    def _action_enabled(self, action):
        return self.panel.enabled(
            action, self._progress()[1], self.speech.state, self.on_seek is not None
        )

    def _preview_seek(self, event):
        self.seek_preview = self.panel.layout.position(event.x, self.seek_duration)
        self._update_transport()

    def set_appearance(self, appearance):
        if self.portrait.appearance == appearance:
            return
        previous = self.portrait
        if (previous.appearance.image, previous.appearance.idle) == (
            appearance.image,
            appearance.idle,
        ):
            previous.appearance = appearance
        else:
            self.portrait = Portrait(appearance)
            previous.close()
        self.index = self.displayed_frame = 0
        self.animation_started = time.monotonic()
        self._update_app_icon()
        self._refresh_geometry()

    def resize(self, size):
        size = max(50, min(300, int(size)))
        if self.size == size:
            return
        self.size = size
        self._refresh_geometry()

    def _refresh_geometry(self):
        if self.pressed_action is not None:
            self.transport_canvas.grab_release()
        self.pressed_action = self.seek_preview = self.hovered_action = None
        self.scrubbing = False
        self.width, self.height = self.portrait.canvas_size(self.size)
        # Keep only a few scaled images, regardless of animation length or zoom.
        self.frames.clear()
        self.canvas.configure(width=self.width, height=self.height)
        self.canvas.itemconfigure(self.picture, image=self._frame(self.displayed_frame))
        self._layout_transport()
        self._update_transport()
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
        if self.on_seek is not None:
            position, duration = self.speech.progress
            enabled = "normal" if duration > 0 and self.speech.state != "preparing" else "disabled"
            self.menu.add_command(
                label=f"{format_time(position)} / {format_time(duration)}", state="disabled"
            )
            self.menu.add_command(
                label="Назад на 10 секунд",
                command=lambda: self.on_seek(-10, relative=True),
                state=enabled,
            )
            self.menu.add_command(
                label="Вперёд на 10 секунд",
                command=lambda: self.on_seek(10, relative=True),
                state=enabled,
            )
            if self.on_restart:
                self.menu.add_command(label="С начала", command=self.on_restart, state=enabled)
            self.menu.add_separator()
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
            animations = tk.Menu(self.menu, tearoff=False)
            animations.add_command(label="Ожидание…", command=lambda: self._choose_image("idle"))
            animations.add_command(label="Разговор…", command=lambda: self._choose_image("talk"))
            animations.add_separator()
            animations.add_command(label="Один портрет…", command=self._choose_image)
            self.menu.add_cascade(label="Анимации персонажа", menu=animations)
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

    def _choose_image(self, state="image"):
        title = {"idle": "Анимация ожидания", "talk": "Анимация разговора", "image": "Один портрет"}
        path = filedialog.askopenfilename(
            parent=self.root,
            title=title[state],
            filetypes=[
                ("Изображения", "*.gif *.png *.jpg *.jpeg *.webp *.bmp"),
                ("Все файлы", "*.*"),
            ],
        )
        if path:
            self.on_option(state, path)

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
        if self.hide_job is not None:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        self.hovering = True
        self._update_control()

    def _leave(self, event):
        if self.hide_job is not None:
            self.root.after_cancel(self.hide_job)
        # Crossing the small gap into the detached panel must not hide it.
        self.hide_job = self.root.after(180, self._finish_leave)

    def _finish_leave(self):
        self.hide_job = None
        if self.pressed_action is not None:
            return
        x, y = self.root.winfo_pointerxy()
        self.hovering = False
        for window in (self.root, self.dock):
            if window.winfo_ismapped():
                left, top = window.winfo_rootx(), window.winfo_rooty()
                if (
                    left <= x < left + window.winfo_width()
                    and top <= y < top + window.winfo_height()
                ):
                    self.hovering = True
                    break
        if not self.hovering:
            self.hovered_action = None
        self._update_control()

    def _motion(self, event):
        if not self.hovering:
            self._enter(event)
        action = self._transport_action(event)
        self.hovered_action = action
        cursor = "hand2" if self._action_enabled(action) else "arrow" if action else "fleur"
        event.widget.configure(cursor=cursor)
        self._update_transport()

    def _press(self, event):
        if not self.hovering:
            self._enter(event)
        self.pressed_action = self._transport_action(event)
        self.scrubbing = self.pressed_action == "seek" and self._action_enabled("seek")
        if self.scrubbing:
            self.seek_duration = self._progress()[1]
            self._preview_seek(event)
        if self.pressed_action is not None:
            self.drag_origin = None
            event.widget.grab_set()
        else:
            self.drag_origin = (
                event.x_root,
                event.y_root,
                self.root.winfo_x(),
                self.root.winfo_y(),
            )
        self.dragging = False
        self._update_transport()

    def _drag(self, event):
        if self.scrubbing:
            self._preview_seek(event)
            return
        if self.drag_origin is None or self.pressed_action is not None:
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
        action = self.pressed_action
        if self.scrubbing:
            self._preview_seek(event)
        target = self.seek_preview
        hit = self._transport_action(event)
        if action is not None:
            event.widget.grab_release()
        self.drag_origin = None
        self.dragging = False
        self.pressed_action = None
        self.scrubbing = False
        self.seek_preview = None
        if action == "seek" and target is not None and self._action_enabled("seek"):
            self.on_seek(target)
        elif action in {"back", "forward"} and action == hit and self._action_enabled(action):
            self.on_seek(-10 if action == "back" else 10, relative=True)
        elif action == "play" and hit == "play":
            if self.speech.state in {"preparing", "playing"}:
                self.on_stop()
            else:
                self.on_start()
        self._update_control()
        self._leave(event)

    def _update_control(self):
        self._update_transport()

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
                    "paused": " · пауза",
                    "error": " · ошибка, см. терминал",
                }.get(state, "")
            )
            self._update_control()
            self.last_state = state
        self._update_transport()
        self._animate(state, now)
        self.root.after(10, self._tick)

    def _animate(self, speech_state, now):
        state = "talk" if speech_state == "playing" else "idle"
        if self.animation_state != state:
            self.animation_state = state
            self.animation_started = now
            self.displayed_frame = -1
        # Use elapsed time so decoding, resizing or dragging cannot slow the clip.
        self.index = self.portrait.frame_at(now - self.animation_started, state)
        if self.displayed_frame != self.index:
            self.canvas.itemconfigure(self.picture, image=self._frame(self.index))
            self.displayed_frame = self.index
