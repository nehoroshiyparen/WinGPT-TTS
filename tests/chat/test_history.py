import tempfile
import unittest
from pathlib import Path

from wgtts.chat.history import HistoryGapError, HistoryStore, merge_window
from wgtts.chat.models import Message, ScanResult, Snapshot
from wgtts.chat.scanner import scan_chat


def message(role, text, key="", complete=True):
    return Message(role, text, complete=complete, source_key=key)


class MergeTests(unittest.TestCase):
    def test_overlapping_windows_keep_order_and_local_ids(self):
        original = message("user", "Первый", "u1")
        history = [original, message("assistant", "Ответ", "a1")]
        added = merge_window(
            history, [message("assistant", "Ответ", "a1"), message("user", "Второй", "u2")]
        )
        self.assertEqual(added, 1)
        self.assertEqual([item.text for item in history], ["Первый", "Ответ", "Второй"])
        self.assertEqual(history[0].id, original.id)

    def test_remounted_accessibility_nodes_match_by_content(self):
        history = [message("user", "Один", "old1"), message("assistant", "Два", "old2")]
        merge_window(history, [message("assistant", "Два", "new2"), message("user", "Три", "new3")])
        self.assertEqual([item.text for item in history], ["Один", "Два", "Три"])

    def test_identical_replies_at_different_positions_are_preserved(self):
        history = [
            message("user", "Один", "u1"),
            message("assistant", "Да", "a1"),
            message("user", "Два", "u2"),
        ]
        merge_window(
            history,
            [
                message("assistant", "Да", "a1"),
                message("user", "Два", "u2"),
                message("assistant", "Да", "a2"),
            ],
        )
        self.assertEqual([item.text for item in history], ["Один", "Да", "Два", "Да"])

    def test_existing_window_does_not_duplicate_messages(self):
        history = [
            message("user", "Один", "u1"),
            message("assistant", "Два", "a1"),
            message("user", "Три", "u2"),
        ]
        self.assertEqual(
            merge_window(
                history, [message("user", "Один", "u1"), message("assistant", "Два", "a1")]
            ),
            0,
        )
        self.assertEqual(len(history), 3)

    def test_growing_draft_retains_selection_id(self):
        draft = message("assistant", "Начало", "draft", False)
        history = [message("user", "Вопрос", "u1"), draft]
        merge_window(
            history,
            [
                message("user", "Вопрос", "u1"),
                message("assistant", "Начало и конец", "draft", True),
            ],
        )
        self.assertEqual(history[-1].id, draft.id)
        self.assertEqual(history[-1].text, "Начало и конец")
        self.assertTrue(history[-1].complete)

    def test_gap_is_reported_instead_of_silent_concatenation(self):
        history = [message("user", "Один")]
        with self.assertRaises(HistoryGapError):
            merge_window(history, [message("user", "Десять")])
        self.assertEqual(len(history), 1)

    def test_ambiguous_repeated_turn_is_not_silently_deduplicated(self):
        history = [message("user", "Ещё", "u1"), message("assistant", "Да", "a1")]
        with self.assertRaises(HistoryGapError):
            merge_window(history, [message("user", "Ещё", "u2"), message("assistant", "Да", "a2")])


class StoreTests(unittest.TestCase):
    def test_growing_chat_does_not_create_an_archive_for_each_reply(self):
        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".history-test-"
        ) as directory:
            store = HistoryStore(Path(directory))
            result = ScanResult("Чат", [], True, True, 0, session_key="same-chat")
            paths = set()
            for index in range(40):
                result.messages.extend(
                    [message("user", f"Вопрос {index}"), message("assistant", f"Ответ {index}")]
                )
                result.expected_users = index + 1
                paths.add(store.save(result))
            files = list(Path(directory).glob("*.json"))
            self.assertEqual(len(paths), 1)
            self.assertEqual(len(files), 3)
            self.assertEqual(
                sum(path.stat().st_size for path in files),
                3 * (Path(directory) / "latest.json").stat().st_size,
            )

    def test_partial_refresh_keeps_last_complete_checkpoint(self):
        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".history-test-"
        ) as directory:
            store = HistoryStore(Path(directory))
            full = ScanResult(
                "Чат",
                [message("user", "Вопрос"), message("assistant", "Ответ")],
                True,
                True,
                1,
                session_key="chat",
            )
            store.save(full)
            checkpoint = next(Path(directory).glob("full-*.json"))
            before = checkpoint.read_bytes()
            store.save(
                ScanResult(
                    "Чат", [message("assistant", "Ответ")], False, True, 1, session_key="chat"
                )
            )
            self.assertEqual(checkpoint.read_bytes(), before)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 3)

    def test_unicode_and_selection_ids_survive_restart(self):
        result = ScanResult("Проверка", [message("user", "Привет\n• яблоко")], True, True, 1)
        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".history-test-"
        ) as directory:
            store = HistoryStore(Path(directory))
            archived = store.save(result)
            loaded = HistoryStore(Path(directory)).load()
            self.assertTrue(archived.exists())
            self.assertEqual(loaded.messages[0].text, "Привет\n• яблоко")
            self.assertEqual(loaded.messages[0].id, result.messages[0].id)
            self.assertTrue(loaded.complete)

    def test_reaching_both_ends_is_not_enough_when_users_are_missing(self):
        result = ScanResult("Проверка", [message("user", "Один")], True, True, 2)
        self.assertFalse(result.complete)

    def test_unknown_total_is_not_claimed_complete(self):
        self.assertFalse(ScanResult("Проверка", [], True, True).complete)

    def test_a_draft_is_not_claimed_complete(self):
        result = ScanResult(
            "Проверка",
            [message("user", "Вопрос"), message("assistant", "Пишется", complete=False)],
            True,
            True,
            1,
        )
        self.assertFalse(result.complete)


class FakeReader:
    def __init__(self):
        self.percent = 50
        self.calls = []
        self.reads = 0

    def scroll_to(self, percent, session_key=None):
        self.calls.append(percent)
        self.percent = 0 if percent == 0 else 100 if percent >= 99.95 else 50

    def snapshot(self):
        self.reads += 1
        a = message("user", "Один", "u1")
        b = message("assistant", "Первый ответ", "a1")
        c = message("user", "Два", "u2")
        d = message("assistant", "Второй ответ", "a2")
        messages = [a, b] if self.percent == 0 else [c, d] if self.percent == 100 else [b, c]
        return Snapshot("Чат", "session", messages, self.percent, 50, 2)


class ScannerTests(unittest.TestCase):
    def test_collects_whole_chat_and_restores_original_position(self):
        reader = FakeReader()
        result = scan_chat(reader, delay=0)
        self.assertTrue(result.complete)
        self.assertEqual(result.user_count, 2)
        self.assertEqual(
            [item.text for item in result.messages], ["Один", "Первый ответ", "Два", "Второй ответ"]
        )
        self.assertEqual(reader.calls[-1], 50)

    def test_failed_read_restores_position_and_preserves_partial_history(self):
        class FailingReader(FakeReader):
            def snapshot(self):
                if self.reads == 2:
                    raise RuntimeError("Элемент исчез")
                return super().snapshot()

        reader = FailingReader()
        result = scan_chat(reader, delay=0)
        self.assertFalse(result.complete)
        self.assertEqual(reader.calls[-1], 50)
        self.assertEqual(len(result.messages), 2)
        self.assertIn("Элемент исчез", result.warnings)

    def test_cancellation_restores_position(self):
        class CancelledReader(FakeReader):
            def snapshot(self):
                if self.reads == 2:
                    raise KeyboardInterrupt
                return super().snapshot()

        reader = CancelledReader()
        result = scan_chat(reader, delay=0)
        self.assertFalse(result.complete)
        self.assertEqual(reader.calls[-1], 50)
        self.assertTrue(any("прерван" in text for text in result.warnings))

    def test_old_cancelled_commentary_is_finalized_by_a_later_user_turn(self):
        class CommentaryReader(FakeReader):
            def snapshot(self):
                snapshot = super().snapshot()
                for item in snapshot.messages:
                    if item.source_key == "a1":
                        item.complete = False
                return snapshot

        result = scan_chat(CommentaryReader(), delay=0)
        self.assertTrue(result.complete)
        self.assertTrue(result.messages[1].complete)


if __name__ == "__main__":
    unittest.main()
