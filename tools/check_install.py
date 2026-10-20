"""Check the local editable installation without importing desktop services."""

import argparse
import hashlib
import importlib.util
from pathlib import Path

REQUIRED_MODULES = ("tkinter", "PIL", "pywinauto", "sounddevice", "prompt_toolkit", "wgtts")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--write-stamp", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if any(importlib.util.find_spec(name) is None for name in REQUIRED_MODULES):
        return 1
    package = importlib.util.find_spec("wgtts")
    expected = root / "src" / "wgtts" / "__init__.py"
    if package.origin is None or Path(package.origin).resolve() != expected:
        return 1
    metadata_hash = hashlib.sha256((root / "pyproject.toml").read_bytes()).hexdigest()
    stamp = root / ".venv" / ".wgtts-install"
    if args.write_stamp:
        stamp.write_text(metadata_hash, encoding="ascii")
        return 0
    return int(not stamp.is_file() or stamp.read_text(encoding="ascii").strip() != metadata_hash)


if __name__ == "__main__":
    raise SystemExit(main())
