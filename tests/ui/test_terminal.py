"""Regression: read/list/show/say must share one numbered collection."""

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from wgtts.chat.history import HistoryStore
from wgtts.chat.models import Message, ScanResult, Snapshot
from wgtts.ui.terminal import Terminal


def msg(role, text, key, complete=True):
    return Message(role, text, complete, key)


class TerminalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".terminal-test-"
        )
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "history"
        for target in (
            "wgtts.ui.terminal.ChatReader",
            "wgtts.ui.terminal.SpeechService",
            "wgtts.ui.terminal.AutoReader",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.old = [
            msg("user", "Первый", "u1"),
            msg("assistant", "Ответ 1", "a1"),
            msg("user", "Второй", "u2"),
            msg("assistant", "Ответ 2", "a2"),
        ]
        self.output = StringIO()
        self.capture = redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def make_terminal(self, session="chat"):
        HistoryStore(self.directory).save(
            ScanResult("Чат", self.old, True, True, 2, session_key=session)
        )
        terminal = Terminal(self.directory)
        terminal.speech.progress = (0.0, 0.0)
        terminal.speech.resume.return_value = False
        self.addCleanup(terminal.close)
        return terminal

    def test_read_appends_new_answer_and_say_uses_printed_number(self):
        terminal = self.make_terminal()
        incoming = self.old[2:] + [
            msg("user", "Третий", "u3"),
            msg("assistant", "Свежий ответ", "a3"),
        ]
        terminal.reader.snapshot.return_value = Snapshot("Чат", "chat", incoming, 100, 10, 3)
        terminal.execute("read")
        self.assertIn(">  6 Ассистент", self.output.getvalue())
        terminal.execute("list")
        terminal.execute("show 6")
        terminal.execute("say 6")
        terminal.speech.say.assert_called_once_with("Свежий ответ", expected_revision=None)
        saved = HistoryStore(self.directory).load()
        self.assertEqual(len(saved.messages), 6)
        self.assertTrue(saved.complete)
        self.assertEqual(saved.session_key, "chat")
        self.assertEqual(saved.messages[0].id, self.old[0].id)

    def test_legacy_saved_history_is_extended_using_unique_overlap(self):
        terminal = self.make_terminal(session=None)
        incoming = [
            msg("user", "Второй", "remounted-u2"),
            msg("assistant", "Ответ 2", "remounted-a2"),
            msg("user", "Новый вопрос", "u3"),
            msg("assistant", "Новый ответ", "a3"),
        ]
        terminal.reader.snapshot.return_value = Snapshot("Чат", "chat", incoming, 100, 10, 3)
        terminal.execute("read")
        self.assertEqual(len(terminal.history.messages), 6)
        self.assertEqual(terminal.history.session_key, "chat")
        terminal.execute("say")
        terminal.speech.say.assert_called_once_with("Новый ответ", expected_revision=None)

    def test_same_title_different_chat_is_not_merged(self):
        terminal = self.make_terminal()
        incoming = [msg("user", "Второй", "other-u2"), msg("assistant", "Ответ 2", "other-a2")]
        terminal.reader.snapshot.return_value = Snapshot(
            "Чат", "another-chat", incoming, 100, 10, 10
        )
        terminal.execute("read")
        terminal.execute("say 2")
        self.assertEqual(len(terminal.history.messages), 2)
        self.assertEqual(terminal.history.session_key, "another-chat")
        self.assertGreaterEqual(len(list(self.directory.glob("*.json"))), 3)
        terminal.speech.say.assert_called_once_with("Ответ 2", expected_revision=None)

    def test_reading_old_region_keeps_cached_tail_and_numbers(self):
        terminal = self.make_terminal()
        terminal.reader.snapshot.return_value = Snapshot("Чат", "chat", self.old[:2], 0, 10, 2)
        terminal.execute("read")
        self.assertEqual(len(terminal.history.messages), 4)
        self.assertEqual(terminal.selected, 1)
        terminal.execute("say")
        terminal.speech.say.assert_called_once_with("Ответ 1", expected_revision=None)

    def test_completed_draft_is_refreshed_before_say(self):
        self.old[-1].complete = False
        terminal = self.make_terminal()
        incoming = [msg("user", "Второй", "u2"), msg("assistant", "Ответ 2 целиком", "a2")]
        terminal.reader.snapshot.return_value = Snapshot("Чат", "chat", incoming, 100, 10, 2)
        terminal.execute("read")
        terminal.execute("say 4")
        self.assertTrue(terminal.history.messages[-1].complete)
        self.assertEqual(terminal.history.messages[-1].id, self.old[-1].id)
        terminal.speech.say.assert_called_once_with("Ответ 2 целиком", expected_revision=None)

    def test_empty_read_retains_selection_and_saved_history(self):
        terminal = self.make_terminal()
        before = (self.directory / "latest.json").read_bytes()
        terminal.reader.snapshot.return_value = Snapshot("Чат", "chat", [], 100, 10, 2)
        terminal.execute("read")
        self.assertEqual(terminal.selected, 3)
        self.assertEqual((self.directory / "latest.json").read_bytes(), before)

    def test_restart_selects_latest_finished_answer_instead_of_saved_draft(self):
        self.old[-1].complete = False
        terminal = self.make_terminal()
        self.assertEqual(terminal.selected, 1)
        terminal.execute("say")
        terminal.speech.say.assert_called_once_with("Ответ 1", expected_revision=None)

    def test_volume_is_applied_and_persisted(self):
        terminal = self.make_terminal()
        terminal.execute("set volume 35")
        self.assertEqual(terminal.settings.data["volume"], 35)
        from wgtts.settings import Settings

        self.assertEqual(Settings(self.directory.parent / "settings.json").data["volume"], 35)
        for value in ("-1", "101", "nan"):
            with self.assertRaises(ValueError):
                terminal.execute("set volume " + value)
        self.assertEqual(terminal.settings.data["volume"], 35)

    def test_stop_bypasses_pending_command_queue(self):
        terminal = self.make_terminal()
        terminal.enqueue("scan")
        terminal.submit_command("stop")
        terminal.speech.stop.assert_called_once()
        self.assertEqual(terminal.commands.qsize(), 1)

    def test_late_output_after_close_does_not_touch_restored_console(self):
        terminal = self.make_terminal()
        terminal.close()
        self.output.seek(0)
        self.output.truncate()
        terminal.notify("Запоздалый ответ сервиса")
        terminal.write("Запоздалый результат команды")
        self.assertEqual(self.output.getvalue(), "")

    def test_start_resumes_paused_recording_and_auto_instead_of_saying_selection(self):
        terminal = self.make_terminal()
        terminal.auto_mode = True
        terminal.speech.resume.return_value = True
        terminal.execute("start")
        terminal.speech.resume.assert_called_once_with(expected_revision=None)
        terminal.speech.say.assert_not_called()
        terminal.auto.set_enabled.assert_called_with(True)

    def test_widget_resume_and_seek_bypass_long_command_queue(self):
        terminal = self.make_terminal()
        terminal.enqueue("scan")
        terminal.speech.state = "paused"
        terminal.speech.resume.return_value = True
        terminal.submit_command("start")
        terminal.speech.resume.assert_called_once_with()
        terminal.speech.seek.return_value = 30.0
        terminal.submit_command("seek +10")
        terminal.speech.seek.assert_called_once_with(10, relative=True)
        self.assertEqual(terminal.commands.qsize(), 1)

    def test_seek_commands_support_absolute_time_and_relative_offsets(self):
        terminal = self.make_terminal()
        terminal.speech.seek.return_value = 90.0
        for command, seconds, relative in (
            ("seek 1:30", 90, False),
            ("seek 90", 90, False),
            ("seek -10", -10, True),
            ("seek +10", 10, True),
        ):
            with self.subTest(command=command):
                terminal.execute(command)
                terminal.speech.seek.assert_called_with(seconds, relative=relative)
        for command in ("seek", "seek nan", "seek 1:60", "seek 1 2"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                terminal.execute(command)

    def test_status_shows_position_and_restart_uses_current_recording(self):
        terminal = self.make_terminal()
        terminal.speech.progress = (90.0, 180.0)
        terminal.speech.state = "paused"
        self.assertIn("пауза · 1:30 / 3:00", terminal.screen_status())
        terminal.execute("status")
        self.assertIn("Запись: 1:30 / 3:00", self.output.getvalue())
        terminal.speech.restart.return_value = True
        terminal.execute("restart")
        terminal.speech.restart.assert_called_once_with(expected_revision=None)
        terminal.speech.say.assert_not_called()


if __name__ == "__main__":
    unittest.main()
