"""Client for the installed Applio Desktop REST API (not Gradio)."""

import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from wgtts.settings import local_url


class ApplioError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


GENERATED_AUDIO = re.compile(
    r"tts_(?:rvc_)?output_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.wav\Z", re.I
)


class ApplioClient:
    def __init__(self, url="http://127.0.0.1:8000", timeout=10, *, notify=print):
        self.url = local_url(url)
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))
        self.notify = notify

    def _cleanup_outputs(self, job):
        """Only paths explicitly returned for a job submitted by this client."""
        result = job.get("result")
        result = result if isinstance(result, dict) else {}
        outputs = [job.get("outputFile"), result.get("ttsIntermediate")]
        outputs = [
            Path(value)
            for value in outputs
            if isinstance(value, str) and GENERATED_AUDIO.fullmatch(Path(value).name)
        ]
        if not outputs:
            return
        try:
            health = self.request("health")
            runtime = Path(health["repoRoot"])
            if not runtime.is_absolute():
                return
            audio_root = (runtime / "assets" / "audios").resolve()
            for value in outputs:
                candidate = value if value.is_absolute() else runtime / value
                # Resolving parent/path also rejects traversal and symlinks
                # pointing outside the generated-audio folder. Never recurse.
                if candidate.is_symlink() or candidate.resolve().parent != audio_root:
                    continue
                candidate.unlink(missing_ok=True)
        except (ApplioError, OSError, KeyError, TypeError, ValueError) as error:
            self.notify(f"Не удалось убрать временное аудио своей задачи Applio: {error}")

    def request(self, path, payload=None, *, raw=False):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.url + "/api/" + path,
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = response.read()
            return content if raw else json.loads(content)
        except HTTPError as error:
            detail = error.read(2000).decode("utf-8", errors="replace")
            raise ApplioError(f"Applio: HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise ApplioError(
                f"Applio недоступен по {self.url}. Запусти Applio. ({error})"
            ) from error
        except (ValueError, UnicodeError) as error:
            raise ApplioError("Applio вернул неожиданный ответ API.") from error

    def discover(self):
        health = self.request("health")
        if not health.get("ok"):
            raise ApplioError("Applio пока не готов.")
        response = self.request("jobs")
        jobs = response.get("jobs", [])
        successful = [
            job
            for job in jobs
            if job.get("type") == "tts"
            and job.get("status") == "done"
            and job.get("params", {}).get("pthPath")
        ]
        if not successful:
            raise ApplioError(
                "В Applio нет успешной TTS-задачи с моделью. Сначала создай одну в Applio или задай voice и model через set."
            )
        latest = max(successful, key=lambda job: job.get("createdAt", ""))
        return {
            key: value
            for key, value in latest["params"].items()
            if key not in {"ttsText", "ttsFile", "outputPath", "outputTTSPath", "outputRVCPath"}
        }

    def synthesize(self, text, params, directory: Path, cancel, *, timeout=600):
        if cancel.is_set():
            raise Cancelled()
        payload = {**params, "ttsText": text, "ttsFile": "", "exportFormat": "WAV"}
        response = self.request("tts", payload)
        job = response.get("job", response)
        job_id = job.get("id") or response.get("jobId")
        if not job_id:
            raise ApplioError("Applio не вернул номер задачи TTS.")
        job_path = "jobs/" + quote(str(job_id), safe="")
        finished = False
        try:
            deadline = time.monotonic() + timeout
            while True:
                if cancel.is_set():
                    raise Cancelled()
                if time.monotonic() > deadline:
                    raise ApplioError("Applio не завершил озвучку за 10 минут.")
                response = self.request(job_path)
                job = response.get("job", response)
                if job.get("id") is not None and str(job["id"]) != str(job_id):
                    raise ApplioError("Applio вернул другую задачу вместо запрошенной.")
                status = job.get("status")
                if status == "done":
                    finished = True
                    break
                if status in {"error", "failed", "stopped", "cancelled", "canceled", "interrupted"}:
                    finished = True
                    raise ApplioError("Озвучка Applio: " + str(job.get("error") or status)[-1500:])
                cancel.wait(0.25)
            if cancel.is_set():
                raise Cancelled()
            output = job.get("outputFile")
            if not output:
                raise ApplioError("Applio закончил задачу без звукового файла.")
            content = self.request("audio/raw?" + urlencode({"path": output}), raw=True)
            if content[:4] != b"RIFF" or content[8:12] != b"WAVE":
                raise ApplioError("Applio вернул файл, который не является WAV.")
            if cancel.is_set():
                raise Cancelled()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / ("speech-" + uuid4().hex + ".wav")
            created = False
            try:
                with path.open("xb") as destination:
                    created = True
                    destination.write(content)
            except BaseException:
                if created:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
            return path
        finally:
            # Stop only the job submitted by this call, including timeout/errors.
            if not finished:
                try:
                    self.request(job_path + "/stop", {})
                    response = self.request(job_path)
                    job = response.get("job", response)
                except ApplioError:
                    pass
            if str(job.get("id", "")) == str(job_id):
                self._cleanup_outputs(job)
