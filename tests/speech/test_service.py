import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from wgtts.speech.service import SpeechService

WAV = b"RIFF0000WAVEtest"


class ServiceTests(unittest.TestCase):
    def test_generated_local_audio_is_removed_after_playback_or_error(self):
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
                self.assertFalse(path.exists())

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


if __name__ == "__main__":
    unittest.main()
