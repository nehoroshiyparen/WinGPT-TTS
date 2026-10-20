"""Parse CLI arguments before loading Windows UI Automation and desktop code."""

import argparse
import shlex
import sys
from pathlib import Path

from wgtts.branding import APP_COMMAND, APP_NAME
from wgtts.paths import DATA_DIR


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog=APP_COMMAND,
        description=f"{APP_NAME} — чтение и озвучка чата ChatGPT с настраиваемым портретом.",
    )
    parser.add_argument(
        "command", nargs="*", help="Без команды открывается интерактивный терминал."
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "history")
    parser.add_argument("--delay", type=float)
    parser.add_argument("--no-widget", action="store_true", help="Только терминал, без GIF-окна.")
    parser.add_argument(
        "--plain", action="store_true", help="Обычный текстовый режим без отдельного экрана."
    )
    args = parser.parse_args(argv)
    if args.delay is not None and not 0.1 <= args.delay <= 3:
        parser.error("--delay должен быть от 0.1 до 3 секунд")
    from wgtts.chat.reader import ReaderError
    from wgtts.speech.applio import ApplioError
    from wgtts.ui.terminal import Terminal

    try:
        terminal = Terminal(args.data_dir, args.delay)
        try:
            if args.command:
                terminal.execute(shlex.join(args.command))
                if args.command[0] in {"say", "play", "start"}:
                    while terminal.speech.busy:
                        terminal.done.wait(0.1)
                    if terminal.speech.state == "error":
                        return 1
            else:
                terminal.run(widget=not args.no_widget, full_screen=not args.plain)
        finally:
            terminal.close()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    except (ReaderError, ApplioError, ValueError, OSError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    return 0
