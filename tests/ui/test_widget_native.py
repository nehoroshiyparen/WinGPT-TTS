"""The real Windows card must expand without moving or covering its portrait."""

import sys
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from wgtts.ui.portrait import Appearance
from wgtts.ui.widget import SpeechWidget, _work_area


@unittest.skipUnless(sys.platform == "win32", "Windows window geometry and clipping")
class NativeWidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.attributes("-alpha", 0)
        speech = SimpleNamespace(state="paused", progress=(30.0, 120.0))
        self.widget = SpeechWidget(
            "assets/arthas-talk.webp",
            speech,
            lambda: None,
            lambda: None,
            self.root.destroy,
            root=self.root,
            size=120,
            on_seek=lambda *args, **kwargs: None,
            appearance=Appearance(),
        )
        self.root.geometry("+60+80")
        self.root.update()

    def pump(self, seconds=0.35):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.005)

    def open_panel(self):
        self.widget.pressed_action = "panel"
        self.widget._enter(None)
        self.pump()

    def test_single_card_has_compact_controls_at_every_zoom(self):
        w = self.widget
        for size in (50, 80, 100, 150, 260, 300):
            with self.subTest(size=size):
                w.resize(size)
                self.open_panel()
                layout = w.panel.layout
                self.assertIs(w.transport_canvas, w.canvas)
                self.assertEqual(self.root.winfo_width(), w.width)
                self.assertEqual(self.root.winfo_height(), w.height + layout.height)
                self.assertEqual(layout.width, w.width)
                self.assertLessEqual(layout.height, 70)
                for item in (*w.panel.buttons.values(), w.panel.elapsed, w.panel.total):
                    left, top, right, bottom = w.canvas.bbox(item)
                    self.assertGreaterEqual(left, 0)
                    self.assertLessEqual(right, w.width)
                    self.assertGreaterEqual(top, w.height)
                    self.assertLessEqual(bottom, w.height + layout.height)
                if w.width >= 250:
                    name, status = w.canvas.bbox(w.panel.name), w.canvas.bbox(w.panel.status)
                    back = w.canvas.bbox(w.panel.buttons["back"])
                    forward = w.canvas.bbox(w.panel.buttons["forward"])
                    self.assertLessEqual(name[2], back[0])
                    self.assertGreaterEqual(status[0], forward[2])

    def test_motion_has_intermediate_height_keeps_portrait_fixed_and_retracts(self):
        w = self.widget
        top = self.root.winfo_rooty()
        start = time.monotonic()
        w.pressed_action = "panel"
        with patch("wgtts.ui.widget.time.monotonic", return_value=start):
            w._enter(None)
        with patch("wgtts.ui.widget.time.monotonic", return_value=start + 0.08):
            w._update_transport()
            self.root.update_idletasks()
            self.assertGreater(w.drawer_pixels, 0)
            self.assertLess(w.drawer_pixels, w.panel.layout.height)
        self.pump()
        self.assertEqual(self.root.winfo_rooty(), top)
        self.assertEqual(w.canvas.coords(w.picture), [0, 0])
        w.pressed_action, w.hovering = None, False
        self.pump()
        self.assertEqual(w.drawer_pixels, 0)
        self.assertEqual(self.root.winfo_height(), w.height)

    def test_bottom_edge_opens_upward_without_moving_the_portrait(self):
        w = self.widget
        left, top, right, bottom = _work_area(self.root)
        portrait_top = bottom - w.height - 2
        self.root.geometry(f"+60+{portrait_top}")
        self.root.update()
        self.open_panel()
        self.assertTrue(w.drawer_above)
        image_y = w.canvas.coords(w.picture)[1]
        self.assertEqual(self.root.winfo_rooty() + image_y, portrait_top)
        self.assertLessEqual(self.root.winfo_rooty() + self.root.winfo_height(), bottom)
        w.pressed_action, w.hovering = None, False
        self.pump()
        self.assertEqual(self.root.winfo_rooty(), portrait_top)

    def test_modern_card_really_has_rounded_windows_corners(self):
        import ctypes
        from ctypes import wintypes

        gdi32, user32 = ctypes.windll.gdi32, ctypes.windll.user32
        gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        gdi32.CreateRectRgn.restype = wintypes.HANDLE
        user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]
        gdi32.PtInRegion.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_int]
        region = gdi32.CreateRectRgn(0, 0, 0, 0)
        handle = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        try:
            self.assertGreater(user32.GetWindowRgn(handle, region), 0)
            self.assertFalse(gdi32.PtInRegion(region, 0, 0))
            self.assertTrue(gdi32.PtInRegion(region, self.widget.width // 2, 1))
        finally:
            gdi32.DeleteObject(region)


if __name__ == "__main__":
    unittest.main()
