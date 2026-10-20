import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from wgtts.speech.service import SpeechService

WAV = b"RIFF0000WAVEtest"


class ServiceTests(unittest.TestCase):
    def test_generated_local_audio_is_retained_after_playback_or_error(self):
        for fail in (False, True):
            with (
                self.subTest(fail=fail),
                tempfile.TemporaryDirectory(
                    dir=Path(__file__).resolve().parent, prefix=".speech-test-"
                ) as directory,
            ):
                path = Path(directory) / ("speech-" + "1" * 32 + ".wav")
                path.write_bytes(WAV)
                player = Mock()
                if fail:
                    player.start.side_effect = RuntimeError("speaker failed")
                else:
                    player.start.return_value = 0
                client = SimpleNamespace(synthesize=lambda *args: path)
                service = SpeechService(
                    SimpleNamespace(
                        data={
                            "url": "http://127.0.0.1:8000",
                            "tts": {"pthPath": "m", "ttsVoice": "v"},
                        }
                    ),
                    Path(directory),
                    notify=lambda _: None,
                    client_factory=lambda _: client,
                    player=player,
                )
                try:
                    service.say("Text")
                    deadline = time.monotonic() + 2
                    while service.busy and time.monotonic() < deadline:
                        time.sleep(0.005)
                finally:
                    service.close()
                self.assertTrue(path.exists())

    def test_late_cancelled_audio_is_removed_but_unrelated_file_is_kept(self):
        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".speech-test-"
        ) as directory:
            path = Path(directory) / ("speech-" + "2" * 32 + ".wav")
            path.write_bytes(WAV)
            other = Path(directory) / "user-recording.wav"
            other.write_bytes(WAV)
            entered, release = threading.Event(), threading.Event()

            def synthesize(*args):
                entered.set()
                release.wait(2)
                return path

            player = Mock()
            service = SpeechService(
                SimpleNamespace(
                    data={"url": "http://127.0.0.1:8000", "tts": {"pthPath": "m", "ttsVoice": "v"}}
                ),
                Path(directory),
                notify=lambda _: None,
                client_factory=lambda _: SimpleNamespace(synthesize=synthesize),
                player=player,
            )
            try:
                service.say("Text")
                self.assertTrue(entered.wait(2))
                service.stop()
                release.set()
            finally:
                service.close()
            self.assertFalse(path.exists())
            self.assertTrue(other.exists())
            player.start.assert_not_called()

    def test_stop_invalidates_play_commands_waiting_behind_a_scan(self):
        player = Mock()
        client = Mock()
        service = SpeechService(
            SimpleNamespace(
                data={"url": "http://127.0.0.1:8000", "tts": {"pthPath": "m", "ttsVoice": "v"}}
            ),
            Path("unused"),
            notify=lambda _: None,
            client_factory=lambda _: client,
            player=player,
        )
        self.addCleanup(service.close)
        queued_revision = service.cancel_revision
        service.stop()
        service.say("Старый запуск", expected_revision=queued_revision)
        self.assertFalse(service.busy)
        client.synthesize.assert_not_called()

    def test_stop_during_synthesis_prevents_late_playback(self):
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        player = Mock()

        def synthesize(*args):
            entered.set()
            release.wait(2)
            returned.set()
            return Path("late.wav")

        client = SimpleNamespace(synthesize=synthesize)
        service = SpeechService(
            SimpleNamespace(
                data={"url": "http://127.0.0.1:8000", "tts": {"pthPath": "m", "ttsVoice": "v"}}
            ),
            Path("unused"),
            notify=lambda _: None,
            client_factory=lambda _: client,
            player=player,
        )
        self.addCleanup(service.close)
        service.say("Первый")
        self.assertTrue(entered.wait(2))
        service.stop()
        release.set()
        self.assertTrue(returned.wait(2))
        service.close()
        player.start.assert_not_called()

    def test_stop_while_playing_is_immediate_and_clears_pending(self):
        started = threading.Event()
        player = Mock()
        player.start.side_effect = lambda _: (started.set(), 30)[1]
        client = SimpleNamespace(synthesize=lambda *args: Path("voice.wav"))
        service = SpeechService(
            SimpleNamespace(
                data={"url": "http://127.0.0.1:8000", "tts": {"pthPath": "m", "ttsVoice": "v"}}
            ),
            Path("unused"),
            notify=lambda _: None,
            client_factory=lambda _: client,
            player=player,
        )
        self.addCleanup(service.close)
        service.say("Первый")
        self.assertTrue(started.wait(2))
        service.say("Второй", replace=False)
        service.stop()
        self.assertEqual(service.state, "idle")
        self.assertFalse(service.pending)
        player.stop.assert_called()


class RecordingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".speech-test-"
        )
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.settings = SimpleNamespace(
            data={
                "url": "http://127.0.0.1:8000",
                "tts": {"pthPath": "m", "ttsVoice": "v"},
                "volume": 100,
            }
        )
        self.paths = []
        self.client = Mock()
        self.client.synthesize.side_effect = self.synthesize

    def synthesize(self, *args):
        path = self.directory / f"speech-{uuid4().hex}.wav"
        path.write_bytes(WAV)
        self.paths.append(path)
        return path

    def service(self, duration=0):
        player = Mock()
        player.start.return_value = duration
        service = SpeechService(
            self.settings,
            self.directory,
            notify=lambda _: None,
            client_factory=lambda _: self.client,
            player=player,
        )
        self.addCleanup(service.close)
        return service

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(predicate())

    def test_stop_and_replay_reuse_recording_without_synthesis(self):
        service = self.service(duration=30)
        service.say("Тот же ответ")
        self.wait_for(lambda: service.state == "playing")
        path = self.paths[0]
        service.stop()
        self.assertTrue(path.exists())
        service.say("Тот же ответ")
        self.wait_for(lambda: service.state == "playing")
        self.assertEqual(service.player.start.call_count, 2)
        service.player.start.assert_called_with(path)
        self.assertEqual(self.client.synthesize.call_count, 1)

    def test_restart_reuses_recording_and_next_generation_removes_previous(self):
        service = self.service()
        service.say("Первый ответ")
        self.wait_for(lambda: not service.busy)
        service.close()
        first = self.paths[0]
        unrelated = self.directory / "user-recording.wav"
        unrelated.write_bytes(WAV)

        restarted = self.service()
        restarted.say("Первый ответ")
        self.wait_for(lambda: not restarted.busy)
        self.assertEqual(self.client.synthesize.call_count, 1)
        restarted.say("Второй ответ")
        self.wait_for(lambda: not restarted.busy)
        self.assertFalse(first.exists())
        self.assertTrue(self.paths[1].exists())
        self.assertTrue(unrelated.exists())

    def test_volume_reuses_audio_but_voice_settings_trigger_new_generation(self):
        service = self.service()
        service.say("Ответ")
        self.wait_for(lambda: not service.busy)
        self.settings.data["volume"] = 20
        service.say("Ответ")
        self.wait_for(lambda: not service.busy)
        self.assertEqual(self.client.synthesize.call_count, 1)
        self.settings.data["tts"]["ttsRate"] = 20
        service.say("Ответ")
        self.wait_for(lambda: not service.busy)
        self.assertEqual(self.client.synthesize.call_count, 2)
        self.assertFalse(self.paths[0].exists())

    def test_failed_synthesis_keeps_previous_recording(self):
        service = self.service()
        service.say("Готовый ответ")
        self.wait_for(lambda: not service.busy)
        first = self.paths[0]
        self.client.synthesize.side_effect = RuntimeError("Applio unavailable")
        service.say("Новый ответ")
        self.wait_for(lambda: not service.busy)
        self.assertEqual(service.state, "error")
        self.assertTrue(first.exists())
        service.say("Готовый ответ")
        self.wait_for(lambda: not service.busy)
        service.player.start.assert_called_with(first)
        self.assertEqual(self.client.synthesize.call_count, 2)

    def test_cancelled_replacement_keeps_old_audio_and_removes_late_result(self):
        service = self.service()
        service.say("Готовый ответ")
        self.wait_for(lambda: not service.busy)
        first = self.paths[0]
        entered, release = threading.Event(), threading.Event()

        def late(*args):
            entered.set()
            release.wait(2)
            return self.synthesize()

        self.client.synthesize.side_effect = late
        service.say("Отменённый ответ")
        self.assertTrue(entered.wait(2))
        self.assertTrue(first.exists())
        service.stop()
        release.set()
        self.wait_for(lambda: len(self.paths) == 2 and not self.paths[1].exists())
        self.assertTrue(first.exists())
        service.say("Готовый ответ")
        self.wait_for(lambda: not service.busy)
        service.player.start.assert_called_with(first)
        self.assertEqual(self.client.synthesize.call_count, 2)

    def test_playback_error_can_retry_same_recording(self):
        service = self.service()
        service.player.start.side_effect = RuntimeError("speaker disconnected")
        service.say("Ответ")
        self.wait_for(lambda: not service.busy)
        self.assertEqual(service.state, "error")
        self.assertTrue(self.paths[0].exists())
        service.player.start.side_effect = None
        service.say("Ответ")
        self.wait_for(lambda: not service.busy)
        self.assertEqual(service.state, "idle")
        self.assertEqual(self.client.synthesize.call_count, 1)

    def test_applio_receives_labelled_table_rows(self):
        service = self.service()
        service.say("До\n| Товар | Цена |\n| --- | --- |\n| Чай | 50 |\nПосле")
        self.wait_for(lambda: not service.busy)
        text = self.client.synthesize.call_args.args[0]
        self.assertIn("Строка 1. Товар: Чай; Цена: 50.", text)
        self.assertTrue(text.startswith("До\nТаблица."))
        self.assertTrue(text.endswith("Конец таблицы.\nПосле"))
        self.assertNotIn("|", text)


if __name__ == "__main__":
    unittest.main()
