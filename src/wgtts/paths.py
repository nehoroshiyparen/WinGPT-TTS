"""Package resources and writable user files have separate locations."""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PACKAGE_DIR / "assets"

_source_root = PACKAGE_DIR.parent.parent
PROJECT_ROOT = (
    _source_root
    if (_source_root / "pyproject.toml").is_file() and _source_root / "src" / "wgtts" == PACKAGE_DIR
    else None
)
USER_FILES_DIR = PROJECT_ROOT or Path.cwd()
if PROJECT_ROOT is not None:
    DATA_DIR = PROJECT_ROOT / "data"
elif os.name == "nt":
    DATA_DIR = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WinGPT TTS"
    )
else:
    DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "wgtts"


def image_path(value):
    """Retain legacy assets/... settings and allow custom files in the checkout."""
    path = Path(value).expanduser()
    candidate = path if path.is_absolute() else USER_FILES_DIR / path
    if candidate.is_file():
        return candidate
    try:
        relative = candidate.relative_to(USER_FILES_DIR / "assets")
    except ValueError:
        return candidate
    bundled = ASSETS_DIR / relative
    return bundled if bundled.is_file() else candidate


def portable_image_path(value):
    """Store bundled images as assets/... even after they move into the package."""
    path = image_path(value).resolve()
    for base, prefix in ((ASSETS_DIR, Path("assets")), (USER_FILES_DIR, Path())):
        try:
            return (prefix / path.relative_to(base)).as_posix()
        except ValueError:
            continue
    return str(value)
