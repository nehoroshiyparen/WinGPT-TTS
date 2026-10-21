"""Transport gestures must seek audio without dragging the portrait window."""

import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wgtts.ui.transport import DrawerMotion, TransportLayout, TransportPanel
from wgtts.ui.widget import SpeechWidget


class WidgetTransportTests(unittest.TestCase):
    def setUp(self):
        self.widget = SpeechWidget.__new__(SpeechWidget)
        widget = self.widget
        widget.canvas, widget.root, widget.portrait = Mock(), Mock(), Mock()
        widget.root.winfo_x.return_value = widget.root.winfo_y.return_value = 0
        widget.width, widget.height = 249, 221
        widget.portrait.content_box.return_value = (9, 9, 240, 212)
        widget.size = 100
        widget.speech = SimpleNamespace(state="paused", progress=(30.0, 120.0))
        widget.on_seek, widget.on_start, widget.on_stop = Mock(), Mock(), Mock()
        widget.panel = SimpleNamespace(
            layout=TransportLayout.for_portrait((9, 9, 240, 212)),
            enabled=TransportPanel.enabled,
            update=Mock(),
        )
        widget.transport_canvas = widget.canvas
        widget.drawer = DrawerMotion(value=1, target=1, origin=1)
        widget.drawer_pixels = widget.panel.layout.height
        widget.drawer_above = False
        widget.panel_y = widget.height
        widget._apply_drawer = Mock()
        widget._choose_direction = Mock()
        widget._position_card = Mock()
        widget.hovering = True
        widget.dragging = widget.scrubbing = False
        widget.pressed_action = widget.seek_preview = widget.drag_origin = None
        widget.hovered_action = widget.hide_job = None
        widget.seek_duration = 0

    def event(self, x, y, *, surface=None):
        return SimpleNamespace(
            x=x,
            y=y + self.widget.panel_y if surface is None else y,
            x_root=x + 50,
            y_root=y + 50,
            widget=surface if surface is not None else self.widget.transport_canvas,
        )

    def test_clicking_timeline_seeks_to_fraction_without_starting_audio(self):
        widget = self.widget
        left, right, y = widget.panel.layout.track
        event = self.event((left + right) / 2, y)
        widget._press(event)
        widget._release(event)
        widget.on_seek.assert_called_once_with(60.0)
        widget.on_start.assert_not_called()
        widget.on_stop.assert_not_called()
        self.assertFalse(widget.dragging)

    def test_dragging_timeline_previews_clamps_and_commits_only_on_release(self):
        widget = self.widget
        left, right, y = widget.panel.layout.track
        event = self.event(left, y)
        widget._press(event)
        widget._drag(self.event(right + 50, y))
        self.assertEqual(widget.seek_preview, 120.0)
        widget.on_seek.assert_not_called()
        widget._drag(self.event(left - 50, y))
        self.assertEqual(widget.seek_preview, 0.0)
        widget._release(event)
        widget.on_seek.assert_called_once_with(0.0)
        widget.root.geometry.assert_not_called()

    def test_back_and_forward_buttons_seek_relative_to_current_position(self):
        widget = self.widget
        for action, seconds in (("back", -10), ("forward", 10)):
            event = self.event(*widget.panel.layout.centers[action])
            widget._press(event)
            widget._release(event)
            widget.on_seek.assert_called_with(seconds, relative=True)
        self.assertEqual(widget.on_seek.call_count, 2)
        widget.on_start.assert_not_called()

    def test_play_pause_remain_separate_from_timeline_controls(self):
        widget = self.widget
        event = self.event(*widget.panel.layout.centers["play"])
        widget._press(event)
        widget._release(event)
        widget.on_start.assert_called_once_with()
        widget.speech.state = "playing"
        widget._press(event)
        widget._release(event)
        widget.on_stop.assert_called_once_with()
        widget.on_seek.assert_not_called()

    def test_timeline_shows_current_time_and_is_disabled_during_synthesis(self):
        widget = self.widget
        widget._update_transport()
        self.assertEqual(widget.panel.update.call_args.args, (30.0, 120.0, "paused"))
        left, _, y = widget.panel.layout.track
        event = self.event(left, y)
        widget.speech.state = "preparing"
        self.assertEqual(widget._transport_action(event), "seek")
        self.assertFalse(widget._action_enabled("seek"))
        widget.speech.state = "paused"
        widget.speech.progress = (0.0, 0.0)
        self.assertEqual(widget._transport_action(event), "seek")
        self.assertFalse(widget._action_enabled("seek"))

    def test_disabled_controls_and_panel_never_drag_the_window(self):
        widget = self.widget
        layout = widget.panel.layout
        left, _, y = layout.track
        targets = [*layout.centers.values(), (left, y), (layout.x + 4, layout.y + 4)]
        for state, duration in (("preparing", 120), ("idle", 0)):
            widget.speech.state, widget.speech.progress = state, (0, duration)
            for x, y in targets:
                # Play is allowed; all other regions must absorb disabled clicks.
                if layout.hit(x, y) == "play":
                    continue
                event = self.event(x, y)
                widget._press(event)
                self.assertIsNone(widget.drag_origin)
                widget._drag(self.event(x + 25, y + 25))
                widget._release(event)
                self.assertFalse(widget.dragging)
        widget.on_seek.assert_not_called()
        widget.root.geometry.assert_not_called()

    def test_panel_coordinates_are_translated_and_portrait_still_drags(self):
        widget = self.widget
        widget.panel.layout = TransportLayout.for_portrait((4, 4, 120, 106))
        widget._update_transport = Mock()
        event = self.event(*widget.panel.layout.centers["forward"])
        widget._press(event)
        widget._release(event)
        widget.on_seek.assert_called_once_with(10, relative=True)
        portrait_event = self.event(20, 20, surface=widget.canvas)
        self.assertIsNone(widget._transport_action(portrait_event))
        widget._leave(portrait_event)
        self.assertTrue(widget.hovering)
        pending_hide = widget.hide_job
        widget._enter(event)
        widget.root.after_cancel.assert_called_with(pending_hide)

    def test_only_portrait_background_can_drag_the_window(self):
        widget = self.widget
        event = self.event(20, 20, surface=widget.canvas)
        widget._press(event)
        with patch("wgtts.ui.widget.sys.platform", "linux"):
            widget._drag(self.event(40, 50, surface=widget.canvas))
        self.assertEqual((widget.anchor_x, widget.anchor_y), (20, 30))
        widget._position_card.assert_called()
        widget.on_start.assert_not_called()
        widget.on_seek.assert_not_called()

    def test_motion_shows_controls_even_if_initial_enter_event_was_missed(self):
        widget = self.widget
        widget.hovering = False
        widget._motion(self.event(20, 20))
        self.assertTrue(widget.hovering)
        self.assertTrue(widget.panel.update.call_args.kwargs["visible"])


class TransportLayoutTests(unittest.TestCase):
    def test_controls_are_centered_symmetric_and_scale_with_portrait(self):
        normal = TransportLayout.for_portrait((9, 9, 240, 212))
        large = TransportLayout.for_portrait((27, 27, 720, 636))
        for layout in (normal, large):
            back, play, forward = (layout.centers[action] for action in ("back", "play", "forward"))
            self.assertEqual(play[0], layout.x + layout.width / 2)
            self.assertAlmostEqual(play[0] - back[0], forward[0] - play[0])
            for action, (x, y) in layout.centers.items():
                radius = layout.diameter(action) / 2
                self.assertEqual(layout.hit(x + radius - 1, y), action)
                self.assertGreaterEqual(x - radius, layout.x)
                self.assertLessEqual(x + radius, layout.x + layout.width)
        self.assertEqual(large.diameter("play"), normal.diameter("play"))
        self.assertLessEqual(normal.height, 70)
        self.assertEqual(normal.width, 231)

    def test_large_portraits_do_not_make_player_grow_past_its_limit(self):
        capped = TransportLayout.for_portrait((0, 0, 346, 304))
        for box in ((0, 0, 693, 609), (0, 0, 1386, 1218), (0, 0, 5000, 5000)):
            with self.subTest(box=box):
                layout = TransportLayout.for_portrait(box)
                self.assertEqual(layout.height, capped.height)
                self.assertEqual(layout.width, box[2])
                self.assertLessEqual(layout.height, 70)
                self.assertLessEqual(layout.diameter("play"), 36)
                self.assertLessEqual(layout.diameter("back"), 28)

    def test_small_portraits_keep_compact_controls_within_their_width(self):
        for box in ((4, 4, 120, 106), (7, 7, 192, 169), (0, 0, 500, 100)):
            with self.subTest(box=box):
                layout = TransportLayout.for_portrait(box)
                self.assertEqual(layout.width, box[2] - box[0])
                self.assertGreaterEqual(layout.diameter("play"), 27)
                self.assertGreaterEqual(layout.diameter("back"), 20)

    def test_drawer_reverses_midway_without_jumping_and_finishes_exactly(self):
        motion = DrawerMotion()
        motion.to(1, 10)
        middle = motion.at(10.08)
        self.assertGreater(middle, 0)
        self.assertLess(middle, 1)
        motion.to(0, 10.08)
        self.assertEqual(motion.at(10.08), middle)
        self.assertEqual(motion.at(11), 0)


class WidgetAnimationTests(unittest.TestCase):
    def setUp(self):
        self.widget = SpeechWidget.__new__(SpeechWidget)
        w = self.widget
        w.portrait, w.canvas, w.root = Mock(), Mock(), Mock()
        w.frames = OrderedDict()
        w.size, w.picture, w.displayed_frame = 100, 1, 0
        w.animation_state, w.animation_started = "idle", 10.0
        w.portrait.frame_at.return_value = 0

    def test_speech_switches_files_and_pause_preparation_error_animate_idle(self):
        w = self.widget
        w._frame = Mock()
        w.portrait.frame_at.return_value = 3
        w._animate("idle", 10.1)
        w.portrait.frame_at.assert_called_with(unittest.mock.ANY, "idle")
        w._frame.assert_called_with(3)
        w.portrait.frame_at.return_value = 0
        w._animate("playing", 10.2)
        self.assertEqual(w.animation_state, "talk")
        w.portrait.frame_at.assert_called_with(0, "talk")
        for state in ("paused", "preparing", "error", "idle"):
            w._animate(state, 10.3)
            self.assertEqual(w.animation_state, "idle")
        # Changes among silent speech states do not restart the waiting animation.
        self.assertEqual(w.animation_started, 10.3)

    def test_scaled_frame_cache_is_bounded_and_distinguishes_states(self):
        w = self.widget
        with patch("wgtts.ui.widget.ImageTk.PhotoImage", side_effect=lambda *a, **kw: object()):
            first_idle = w._frame(0)
            w.animation_state = "talk"
            self.assertIsNot(w._frame(0), first_idle)
            for frame in range(30):
                w._frame(frame)
            self.assertEqual(len(w.frames), w.FRAME_CACHE_SIZE)
            self.assertIn(("talk", 29), w.frames)


if __name__ == "__main__":
    unittest.main()
