"""Moving bundled assets must preserve existing settings and custom images."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wgtts.paths import ASSETS_DIR, image_path
from wgtts.ui.portrait import Appearance, Portrait


class ResourcePathTests(unittest.TestCase):
    def test_default_portrait_renders_and_keeps_its_portable_name(self):
        appearance = Appearance()
        self.assertEqual(appearance.path, ASSETS_DIR / "arthas.gif")
        self.assertEqual(appearance.to_dict()["image"], "assets/arthas.gif")
        self.assertGreater(len(Portrait(appearance).frames), 1)

    def test_old_absolute_assets_path_resolves_to_the_bundled_portrait(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_path = Path(directory) / "assets" / "arthas.gif"
            with patch("wgtts.paths.USER_FILES_DIR", Path(directory)):
                self.assertEqual(image_path(str(previous_path)), ASSETS_DIR / "arthas.gif")

    def test_existing_custom_image_takes_precedence_over_bundled_image(self):
        with tempfile.TemporaryDirectory() as directory:
            custom_path = Path(directory) / "assets" / "arthas.gif"
            custom_path.parent.mkdir()
            custom_path.write_bytes(b"custom user image")
            with patch("wgtts.paths.USER_FILES_DIR", Path(directory)):
                self.assertEqual(image_path("assets/arthas.gif"), custom_path)

    def test_missing_user_image_is_not_replaced_with_an_unrelated_default(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("wgtts.paths.USER_FILES_DIR", Path(directory)):
                self.assertEqual(
                    image_path("assets/missing.gif"), Path(directory) / "assets" / "missing.gif"
                )


if __name__ == "__main__":
    unittest.main()
