"""Appearance round trips, legacy migration and uncropped framed animation."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image

from wgtts.settings import Settings
from wgtts.ui.portrait import Appearance, Border, Portrait


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".portrait-test-"
        )
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "settings.json"
        self.image = self.directory / "Другой портрет.png"
        Image.new("RGB", (80, 60), "#124896").save(self.image)

    def test_legacy_gif_and_voice_settings_survive_migration(self):
        self.path.write_text(
            json.dumps({"gif": str(self.image), "size": 150, "volume": 35, "tts": {"pitch": -10}}),
            encoding="utf-8",
        )
        settings = Settings(self.path)
        self.assertEqual(settings.appearance.path, self.image)
        settings.set("border-color", "#aabbcc")
        reloaded = Settings(self.path)
        self.assertEqual(reloaded.appearance.border.color, "#aabbcc")
        self.assertEqual(
            (reloaded.data["size"], reloaded.data["volume"], reloaded.data["tts"]),
            (150, 35, {"pitch": -10}),
        )

    def test_saved_preset_restores_both_portrait_and_border_after_restart(self):
        settings = Settings(self.path)
        settings.set("image", str(self.image))
        settings.set("border-color", "#123abc")
        settings.set("border-width", "14")
        expected = settings.appearance
        settings.save_preset("Мой персонаж")
        settings.apply_preset("bare")
        settings = Settings(self.path)
        settings.apply_preset("Мой персонаж")
        self.assertEqual(settings.appearance, expected)
        self.assertEqual(Settings(self.path).appearance, expected)

    def test_invalid_changes_leave_current_appearance_and_saved_file_intact(self):
        settings = Settings(self.path)
        settings.set("image", str(self.image))
        before = self.path.read_bytes()
        appearance = settings.appearance
        for name, value in (
            ("image", str(self.directory / "missing.gif")),
            ("idle", str(self.directory / "missing.webp")),
            ("talk", str(self.directory / "missing.webp")),
            ("border-width", "-1"),
            ("border-width", "31"),
            ("border-width", "nan"),
            ("border-color", "red"),
            ("border-style", "unknown"),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaises((ValueError, OSError)):
                    settings.set(name, value)
                self.assertEqual(settings.appearance, appearance)
                self.assertEqual(self.path.read_bytes(), before)

    def test_adding_width_to_bare_preset_makes_border_visible(self):
        settings = Settings(self.path)
        settings.apply_preset("bare")
        settings.set("border-width", "6")
        self.assertEqual(settings.appearance.border.style, "bevel")

    def test_frame_surrounds_the_entire_original_without_covering_pixels(self):
        portrait = Portrait(Appearance(str(self.image), Border(width=9)))
        framed = portrait.render(0, 100)
        self.assertEqual(framed.size, (98, 78))
        with Image.open(self.image) as source:
            self.assertEqual(
                framed.crop((9, 9, 89, 69)).tobytes(), source.convert("RGBA").tobytes()
            )
        self.assertNotEqual(framed.getpixel((30, 1)), framed.getpixel((30, 76)))

    def test_frame_and_portrait_scale_together_and_no_frame_is_exact_source(self):
        appearance = Appearance(str(self.image), Border(width=10))
        for size in (50, 100, 150, 300):
            portrait = Portrait(appearance)
            self.assertEqual(
                portrait.render(0, size).size, (round(100 * size / 100), round(80 * size / 100))
            )
        bare = Portrait(replace(appearance, border=Border("none"))).render(0, 100)
        with Image.open(self.image) as source:
            self.assertEqual(bare.tobytes(), source.convert("RGBA").tobytes())

    def test_animation_changes_inside_but_frame_is_static(self):
        image = self.directory / "animation.gif"
        first = Image.new("RGB", (80, 60), "red")
        first.save(
            image,
            save_all=True,
            append_images=[Image.new("RGB", (80, 60), "blue")],
            duration=[80, 120],
        )
        portrait = Portrait(Appearance(str(image), Border()))
        before, after = portrait.render(0, 100), portrait.render(1, 100)
        self.assertEqual(portrait.durations, [80, 120])
        self.assertEqual(before.crop((0, 0, 98, 9)).tobytes(), after.crop((0, 0, 98, 9)).tobytes())
        self.assertNotEqual(before.getpixel((40, 40)), after.getpixel((40, 40)))

    def make_animation(self, name, colors, durations, size=(80, 60)):
        path = self.directory / name
        frames = [Image.new("RGB", size, color) for color in colors]
        frames[0].save(
            path, save_all=True, append_images=frames[1:], duration=durations, loop=0, lossless=True
        )
        return path

    def test_two_animations_and_border_survive_preset_switch_and_restart(self):
        idle = self.make_animation("ожидание.webp", ["red", "blue"], [40, 60])
        talk = self.make_animation("разговор.webp", ["green", "yellow"], [80, 120])
        settings = Settings(self.path)
        settings.set("idle", str(idle))
        settings.set("talk", str(talk))
        settings.set("border-color", "#abcdef")
        expected = settings.appearance
        settings.save_preset("Другой персонаж")
        settings.apply_preset("classic")
        settings = Settings(self.path)
        settings.apply_preset("Другой персонаж")
        self.assertEqual(Settings(self.path).appearance, expected)
        with Portrait(expected) as portrait:
            self.assertEqual(portrait.animations["idle"].durations, [40, 60])
            self.assertEqual(portrait.animations["talk"].durations, [80, 120])
            self.assertEqual(portrait.render(0, 100, "idle").getpixel((40, 40)), (255, 0, 0, 255))
            self.assertEqual(portrait.render(0, 100, "talk").getpixel((40, 40)), (0, 128, 0, 255))

    def test_legacy_custom_portrait_waits_on_first_frame_without_bundled_idle(self):
        path = self.make_animation("custom.gif", ["red", "blue"], [40, 60])
        self.path.write_text(json.dumps({"portrait": {"image": str(path)}}), encoding="utf-8")
        settings = Settings(self.path)
        self.assertIsNone(settings.appearance.idle)
        with Portrait(settings.appearance) as portrait:
            self.assertEqual(portrait.frame_at(0.05, "idle"), 0)
            self.assertEqual(portrait.frame_at(0.05, "talk"), 1)
        settings.set("idle", str(path))
        settings.set("image", str(self.image))
        self.assertIsNone(Settings(self.path).appearance.idle)

    def test_old_bundled_portrait_upgrades_both_states_and_keeps_border(self):
        self.path.write_text(
            json.dumps(
                {"portrait": {"image": "assets/arthas.gif", "border": {"color": "#abcdef"}}}
            ),
            encoding="utf-8",
        )
        appearance = Settings(self.path).appearance
        self.assertEqual(appearance.image, "assets/arthas-talk.webp")
        self.assertEqual(appearance.idle, "assets/arthas-idle.webp")
        self.assertEqual(appearance.border.color, "#abcdef")

    def test_different_dimensions_fit_one_canvas_without_crop_or_stretch(self):
        idle = self.directory / "tall.png"
        Image.new("RGB", (20, 60), "red").save(idle)
        with Portrait(Appearance(str(self.image), Border("none"), str(idle))) as portrait:
            waiting = portrait.render(0, 100, "idle")
            talking = portrait.render(0, 100, "talk")
            self.assertEqual(waiting.size, talking.size)
            self.assertEqual(waiting.size, (80, 60))
            self.assertEqual(waiting.getpixel((29, 30)), (17, 21, 29, 255))
            self.assertEqual(waiting.getpixel((30, 0)), (255, 0, 0, 255))
            self.assertEqual(waiting.getpixel((49, 59)), (255, 0, 0, 255))

    def test_timing_loops_and_skips_late_frames_without_changing_speed(self):
        talk = self.make_animation("timing.webp", ["red", "blue", "green"], [20, 40, 60])
        with Portrait(Appearance(str(talk))) as portrait:
            for seconds, frame in [(0, 0), (0.025, 1), (0.1, 2), (0.125, 0), (12.1, 2)]:
                self.assertEqual(portrait.frame_at(seconds), frame)


if __name__ == "__main__":
    unittest.main()
