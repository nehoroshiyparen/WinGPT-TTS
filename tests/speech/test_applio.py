import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from wgtts.speech.applio import ApplioClient, ApplioError, Cancelled

WAV = b"RIFF0000WAVEtest"


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".speech-test-"
        )
        self.addCleanup(self.temp.cleanup)
        self.client = ApplioClient()
        self.cancel = threading.Event()

    def test_real_jobid_envelope_and_audio_download(self):
        request = self.client.request = Mock(
            side_effect=[
                {"jobId": "ours"},
                {"job": {"id": "ours", "status": "done", "outputFile": "assets/audio.wav"}},
                WAV,
            ]
        )
        path = self.client.synthesize("Привет", {"pitch": -10}, Path(self.temp.name), self.cancel)
        self.assertEqual(path.read_bytes(), WAV)
        self.assertEqual(request.call_args_list[0].args[1]["ttsText"], "Привет")
        self.assertEqual(request.call_args_list[0].args[1]["pitch"], -10)
        self.assertEqual(request.call_args_list[1].args[0], "jobs/ours")
        self.assertTrue(request.call_args_list[2].kwargs["raw"])

    def test_cancel_stops_only_submitted_job_and_never_downloads(self):
        def request(path, payload=None, **kwargs):
            if path == "tts":
                self.cancel.set()
                return {"jobId": "ours"}
            return {}

        self.client.request = Mock(side_effect=request)
        with self.assertRaises(Cancelled):
            self.client.synthesize("Text", {}, Path(self.temp.name), self.cancel)
        self.assertEqual(self.client.request.call_args_list[-2].args[0], "jobs/ours/stop")
        self.assertEqual(self.client.request.call_count, 3)

    def test_timeout_cancels_own_job(self):
        self.client.request = Mock(side_effect=[{"jobId": "ours"}, {}, {}])
        with self.assertRaises(ApplioError):
            self.client.synthesize("Text", {}, Path(self.temp.name), self.cancel, timeout=-1)
        self.assertEqual(self.client.request.call_args_list[-2].args[0], "jobs/ours/stop")

    def test_only_this_jobs_generated_applio_files_are_removed(self):
        runtime = Path(self.temp.name) / "runtime"
        audios = runtime / "assets" / "audios"
        audios.mkdir(parents=True)
        output = audios / "tts_rvc_output_11111111-1111-1111-1111-111111111111.wav"
        intermediate = audios / "tts_output_22222222-2222-2222-2222-222222222222.wav"
        other = audios / "tts_output_33333333-3333-3333-3333-333333333333.wav"
        outside = runtime / "tts_output_44444444-4444-4444-4444-444444444444.wav"
        for path in (output, intermediate, other, outside):
            path.write_bytes(WAV)
        self.client.request = Mock(return_value={"repoRoot": str(runtime)})
        self.client._cleanup_outputs(
            {"outputFile": str(output), "result": {"ttsIntermediate": str(intermediate)}}
        )
        self.assertFalse(output.exists())
        self.assertFalse(intermediate.exists())
        self.assertTrue(other.exists())
        self.client._cleanup_outputs({"outputFile": str(outside)})
        self.assertTrue(outside.exists())

    def test_success_copies_audio_before_removing_backend_outputs(self):
        runtime = Path(self.temp.name) / "runtime"
        audios = runtime / "assets" / "audios"
        audios.mkdir(parents=True)
        output = audios / "tts_rvc_output_11111111-1111-1111-1111-111111111111.wav"
        output.write_bytes(WAV)
        self.client.request = Mock(
            side_effect=[
                {"jobId": "ours"},
                {"job": {"id": "ours", "status": "done", "outputFile": str(output)}},
                WAV,
                {"repoRoot": str(runtime)},
            ]
        )
        path = self.client.synthesize("Text", {}, Path(self.temp.name) / "local", self.cancel)
        self.assertEqual(path.read_bytes(), WAV)
        self.assertFalse(output.exists())

    def test_disk_write_failure_does_not_leave_partial_wav(self):
        from unittest.mock import patch

        self.client.request = Mock(
            side_effect=[
                {"jobId": "ours"},
                {"job": {"id": "ours", "status": "done", "outputFile": "assets/audio.wav"}},
                WAV,
            ]
        )
        real_open = Path.open

        class DiskFullWriter:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                self.file = real_open(self.path, "xb")
                self.file.write(b"RIFF")
                return self

            def write(self, content):
                raise OSError("disk full")

            def __exit__(self, *args):
                self.file.close()

        with patch.object(Path, "open", lambda path, mode: DiskFullWriter(path)):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.client.synthesize("Text", {}, Path(self.temp.name), self.cancel)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_failed_job_does_not_return_or_play_audio(self):
        self.client.request = Mock(
            side_effect=[{"jobId": "ours"}, {"job": {"status": "error", "error": "model missing"}}]
        )
        with self.assertRaisesRegex(ApplioError, "model missing"):
            self.client.synthesize("Text", {}, Path(self.temp.name), self.cancel)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_discovery_uses_latest_successful_tts_not_other_jobs(self):
        self.client.request = Mock(
            side_effect=[
                {"ok": True},
                {
                    "jobs": [
                        {
                            "type": "tts",
                            "status": "error",
                            "createdAt": "2026-03",
                            "params": {"pthPath": "bad"},
                        },
                        {
                            "type": "tts",
                            "status": "done",
                            "createdAt": "2026-02",
                            "params": {
                                "pthPath": "arthas",
                                "pitch": -10,
                                "ttsText": "private old text",
                                "ttsVoice": "ru-RU-SvetlanaNeural",
                            },
                        },
                        {
                            "type": "tts",
                            "status": "done",
                            "createdAt": "2026-01",
                            "params": {"pthPath": "older"},
                        },
                    ]
                },
            ]
        )
        params = self.client.discover()
        self.assertEqual(params["pthPath"], "arthas")
        self.assertEqual(params["pitch"], -10)
        self.assertNotIn("ttsText", params)


if __name__ == "__main__":
    unittest.main()
