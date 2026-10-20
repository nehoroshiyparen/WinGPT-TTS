"""Windows regression: playback controls stay visible outside the portrait."""

import sys
import tkinter as tk
import unittest
from types import SimpleNamespace

from wgtts.ui.portrait import Appearance
from wgtts.ui.transport import dock_position
from wgtts.ui.widget import SpeechWidget, _work_area


@unittest.skipUnless(sys.platform == "win32", "Windows transient-window lifecycle")
class NativeWidgetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        # Keep the test unobtrusive while still exercising actual window mapping.
        self.root.attributes("-alpha", 0)
        speech = SimpleNamespace(state="paused", progress=(30.0, 120.0))
        self.widget = SpeechWidget(
            "assets/arthas.gif",
            speech,
            lambda: None,
            lambda: None,
            self.root.destroy,
            root=self.root,
            size=50,
            on_seek=lambda *args, **kwargs: None,
            appearance=Appearance(),
        )
        self.widget.dock.attributes("-alpha", 0)
        self.root.geometry("+60+80")
        self.root.update()

    def show_and_check_panel(self):
        widget = self.widget
        widget.pressed_action = "panel"
        widget._enter(None)
        self.root.update()
        layout, dock = widget.panel.layout, widget.dock
        self.assertTrue(dock.winfo_ismapped(), "Panel stayed unmapped after deiconify")
        self.assertTrue(widget.dock_canvas.winfo_viewable())
        self.assertEqual((dock.winfo_width(), dock.winfo_height()), (layout.width, layout.height))
        expected = dock_position(widget.dock_anchor, _work_area(self.root))
        self.assertEqual((dock.winfo_rootx(), dock.winfo_rooty()), expected)
        portrait_top = self.root.winfo_rooty()
        portrait_bottom = portrait_top + self.root.winfo_height()
        panel_top = dock.winfo_rooty()
        self.assertTrue(panel_top >= portrait_bottom or panel_top + layout.height <= portrait_top)
        self.assertEqual(widget.canvas.find_withtag("transport"), ())
        self.assertEqual(widget.dock_canvas.itemcget(widget.panel.plate, "state"), "normal")
        for item in (*widget.panel.buttons.values(), widget.panel.elapsed, widget.panel.total):
            left, top, right, bottom = widget.dock_canvas.bbox(item)
            self.assertGreaterEqual(left, 0)
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(right, layout.width)
            self.assertLessEqual(bottom, layout.height)

    def test_panel_really_appears_outside_portrait_at_every_size(self):
        for size in (50, 80, 100, 150, 300):
            with self.subTest(size=size):
                self.widget.resize(size)
                self.show_and_check_panel()

    def test_small_large_small_transition_keeps_controls_on_their_own_canvas(self):
        self.show_and_check_panel()
        self.widget.resize(100)
        self.root.update()
        self.assertTrue(self.widget.dock.winfo_ismapped())
        self.assertIs(self.widget.transport_canvas, self.widget.dock_canvas)
        self.show_and_check_panel()
        self.widget.resize(50)
        self.show_and_check_panel()


if __name__ == "__main__":
    unittest.main()
