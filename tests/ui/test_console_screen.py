"""Screen replacement, interactive input and restoration of the host terminal."""

import os
import threading
import time
import unittest
from io import StringIO
from unittest.mock import Mock, patch

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from wgtts.ui.console import ConsoleScreen, ScreenBuffer, interactive_terminal


class ScreenBufferTests(unittest.TestCase):
    def test_new_command_replaces_previous_result(self):
        model = ScreenBuffer()
        model.begin("list")
        model.append("Первый результат\n")
        model.begin("status")
        model.append("Второй результат\n")
        self.assertEqual(model.text, "Второй результат\n")
        self.assertNotIn("Первый", model.text)

    def test_progress_overwrites_one_line_and_preserves_previous_lines(self):
        model = ScreenBuffer()
        model.begin("scan")
        model.append("Читаю чат\n")
        model.append("\rПрокрутка 10%")
        model.append("\rПрокрутка 20%")
        model.append("\nГотово.\n")
        self.assertEqual(model.text, "Читаю чат\nПрокрутка 20%\nГотово.\n")

    def test_large_output_is_bounded_and_marked(self):
        model = ScreenBuffer()
        model.begin("list")
        model.append("old\n" * (model.MAX_LINES + 20))
        model.append("newest")
        self.assertIn("Начало слишком длинного вывода скрыто", model.text)
        self.assertTrue(model.text.endswith("newest"))
        self.assertLessEqual(len(model.lines), model.MAX_LINES)
        model.begin("help")
        self.assertFalse(model.truncated)

    def test_redirected_streams_do_not_enable_screen(self):
        with patch("sys.stdin", StringIO()), patch("sys.stdout", StringIO()):
            self.assertFalse(interactive_terminal())

    def test_real_windows_console_works_even_with_term_dumb(self):
        stream = Mock()
        stream.isatty.return_value = True
        with (
            patch("sys.stdin", stream),
            patch("sys.stdout", stream),
            patch("sys.platform", "win32"),
            patch.dict(os.environ, {"TERM": "dumb"}),
        ):
            self.assertTrue(interactive_terminal())


class ConsoleScreenTests(unittest.TestCase):
    def wait_for(self, condition):
        until = time.monotonic() + 3
        while not condition():
            if time.monotonic() > until:
                self.fail("Screen did not reach the expected state.")
            time.sleep(0.01)

    def make_screen(self, status_provider=lambda: "готов · ручной"):
        pipe = self.enterContext(create_pipe_input())
        stream = StringIO()
        output = Vt100_Output(
            stream,
            get_size=lambda: Size(rows=24, columns=100),
            enable_cpr=False,
            enable_bell=False,
        )
        exit_requested = threading.Event()
        commands = []

        def command(text):
            commands.append(text)
            screen.begin_command(text)
            screen.write(f"Результат {text}\n")
            screen.end_command()

        screen = ConsoleScreen(
            command, exit_requested.set, status_provider, input=pipe, output=output
        )
        self.addCleanup(screen.close)
        screen.start()
        self.assertTrue(screen.ready.wait(3))
        return screen, pipe, stream, exit_requested, commands

    def test_commands_notices_history_and_exit_restore_the_original_screen(self):
        screen, pipe, stream, exit_requested, commands = self.make_screen()
        pipe.send_text("list\r")
        self.wait_for(lambda: screen.model.text == "Результат list\n")
        pipe.send_text("status\r")
        self.wait_for(lambda: screen.model.text == "Результат status\n")
        self.assertNotIn("Результат list", screen.output.text)

        pipe.send_text("sh")
        self.wait_for(lambda: screen.input.text == "sh")
        screen.notify("Фоновое уведомление")
        self.wait_for(lambda: screen.model.notice == "Фоновое уведомление")
        self.assertEqual(screen.input.text, "sh")

        pipe.send_text("\x15\x1b[A")  # Clear draft, then recall the previous command.
        self.wait_for(lambda: screen.input.text == "status")
        self.assertEqual(commands, ["list", "status"])
        pipe.send_text("\x03")
        self.assertTrue(exit_requested.wait(3))
        screen.close()
        self.assertIn("\x1b[?1049h", stream.getvalue())
        self.assertIn("\x1b[?1049l", stream.getvalue())

    def test_long_result_scrolls_while_input_stays_in_place(self):
        screen, pipe, stream, _, _ = self.make_screen()
        screen.begin_command("show 1")
        screen.write("\n".join(f"Строка {index}" for index in range(150)))
        screen.end_command()
        self.wait_for(lambda: "Строка 149" in screen.output.text)
        pipe.send_text("say")
        self.wait_for(lambda: screen.input.text == "say")
        pipe.send_text("\x1b[6~")
        self.wait_for(lambda: screen.output.buffer.document.cursor_position_row > 0)
        self.assertEqual(screen.input.text, "say")
        pipe.send_text("\x1b[1;5F")  # Ctrl+End.
        self.wait_for(lambda: screen.output.buffer.cursor_position == len(screen.output.text))
        self.assertEqual(screen.input.text, "say")
        screen.close()
        self.assertIn("\x1b[?1049l", stream.getvalue())

    def test_render_error_also_restores_the_original_screen(self):
        state = {"fail": False}

        def broken_status():
            if state["fail"]:
                raise ValueError("test render error")
            return "готов"

        # Register our own cleanup because this screen deliberately reports failure.
        pipe = self.enterContext(create_pipe_input())
        stream = StringIO()
        output = Vt100_Output(stream, get_size=lambda: Size(24, 80), enable_cpr=False)
        finished = threading.Event()
        screen = ConsoleScreen(
            lambda _: None, finished.set, broken_status, input=pipe, output=output
        )

        def cleanup():
            state["fail"] = False
            try:
                screen.close()
            except OSError:
                pass

        self.addCleanup(cleanup)
        screen.start()
        self.wait_for(lambda: "\x1b[?1049h" in stream.getvalue())
        state["fail"] = True
        screen.app.invalidate()
        self.assertTrue(finished.wait(3))
        with self.assertRaisesRegex(OSError, "test render error"):
            screen.close()
        self.assertIn("\x1b[?1049l", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
