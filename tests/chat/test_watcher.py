"""Only new, completed and stable answers should be announced."""

import unittest

from wgtts.chat.models import Message, Snapshot
from wgtts.chat.watcher import ReplyTracker


def snapshot(
    text="Ответ", *, turn=1, generating=False, complete=True, position=100, session="chat"
):
    return Snapshot(
        "Chat",
        session,
        [Message("user", "Вопрос"), Message("assistant", text, complete)],
        position,
        10,
        turn,
        generating,
    )


class TrackerTests(unittest.TestCase):
    def test_enabling_auto_does_not_read_old_answer(self):
        tracker = ReplyTracker()
        self.assertIsNone(tracker.observe(snapshot(), 0))
        self.assertIsNone(tracker.observe(snapshot(), 20))

    def test_speaks_new_completed_stable_answer_once(self):
        tracker = ReplyTracker()
        tracker.observe(snapshot(), 0)
        tracker.observe(snapshot("Начало", turn=2, generating=True, complete=False), 1)
        self.assertIsNone(tracker.observe(snapshot("Готово", turn=2), 2))
        self.assertEqual(tracker.observe(snapshot("Готово", turn=2), 5).text, "Готово")
        self.assertIsNone(tracker.observe(snapshot("Готово", turn=2), 10))

    def test_commentary_is_not_spoken_during_generation(self):
        tracker = ReplyTracker()
        tracker.observe(snapshot(generating=True), 0)
        self.assertIsNone(tracker.observe(snapshot(generating=True), 20))
        tracker.observe(snapshot("Финал"), 21)
        self.assertEqual(tracker.observe(snapshot("Финал"), 24).text, "Финал")

    def test_scrollback_and_remounted_nodes_do_not_replay(self):
        tracker = ReplyTracker()
        tracker.observe(snapshot(), 0)
        tracker.observe(snapshot("Старое", position=20), 5)
        self.assertIsNone(tracker.observe(snapshot("Старое", position=20), 50))
        self.assertIsNone(tracker.observe(snapshot(), 51))
        self.assertIsNone(tracker.observe(snapshot(), 60))

    def test_switching_chats_does_not_read_existing_answers(self):
        tracker = ReplyTracker()
        tracker.observe(snapshot(generating=True), 0)
        tracker.observe(snapshot(session="another", turn=9), 10)
        self.assertIsNone(tracker.observe(snapshot(session="another", turn=9), 20))

    def test_changed_text_must_be_stable_again(self):
        tracker = ReplyTracker()
        tracker.observe(snapshot(generating=True), 0)
        tracker.observe(snapshot("A"), 1)
        self.assertIsNone(tracker.observe(snapshot("B"), 3))
        self.assertIsNone(tracker.observe(snapshot("B"), 4))
        self.assertEqual(tracker.observe(snapshot("B"), 6).text, "B")


if __name__ == "__main__":
    unittest.main()
