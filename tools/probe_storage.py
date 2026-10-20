"""Explicit live test: synthesize silently, then verify only own files vanish."""

import time
from pathlib import Path

from wgtts.paths import DATA_DIR
from wgtts.settings import Settings
from wgtts.speech.applio import ApplioClient
from wgtts.speech.service import SpeechService


def main():
    class RecordingClient(ApplioClient):
        job_id = None
        local_path = None

        def request(self, path, payload=None, **kwargs):
            response = super().request(path, payload, **kwargs)
            if path == "tts":
                self.job_id = response.get("jobId") or response.get("id")
            return response

        def synthesize(self, *args, **kwargs):
            self.local_path = super().synthesize(*args, **kwargs)
            return self.local_path

    class SilentPlayer:
        def start(self, path):
            return 0

        def stop(self):
            pass

    settings = Settings()
    client = RecordingClient(settings.data["url"])
    service = SpeechService(
        settings, DATA_DIR / "audio", client_factory=lambda url: client, player=SilentPlayer()
    )
    try:
        service.say("Проверка очистки временных файлов.")
        deadline = time.monotonic() + 60
        while service.busy and time.monotonic() < deadline:
            time.sleep(0.1)
        if service.busy:
            raise RuntimeError("Storage probe timed out")
    finally:
        service.close()
    if client.local_path is None:
        raise RuntimeError("Synthesis did not return a file")
    assert not client.local_path.exists(), "Local temporary file was retained"
    job = client.request("jobs/" + client.job_id)["job"]
    runtime = Path(client.request("health")["repoRoot"])
    for value in [job.get("outputFile"), (job.get("result") or {}).get("ttsIntermediate")]:
        if value:
            assert not (runtime / value).exists(), "Own backend temporary file was retained"
    print("PASS: local WAV, Applio TTS and converted WAV were removed; no audio played.")


if __name__ == "__main__":
    main()
