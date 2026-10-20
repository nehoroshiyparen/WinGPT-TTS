"""Cancel only a fresh test job; does not touch any other Applio task."""

from threading import Event

from wgtts.paths import DATA_DIR
from wgtts.settings import Settings
from wgtts.speech.applio import ApplioClient, Cancelled


def main():
    cancel = Event()

    class CancelAfterSubmit(ApplioClient):
        job_id = None

        def request(self, path, payload=None, **kwargs):
            response = super().request(path, payload, **kwargs)
            if path == "tts":
                self.job_id = response.get("jobId") or response.get("id")
                cancel.set()
            return response

    settings = Settings()
    client = CancelAfterSubmit(settings.data["url"])
    try:
        client.synthesize("Cancellation test.", settings.data["tts"], DATA_DIR / "audio", cancel)
        raise AssertionError("Cancelled synthesis returned audio")
    except Cancelled:
        print("Cancelled test job:", client.job_id)
        response = client.request("jobs/" + client.job_id)
        job = response.get("job", response)
        print("Status:", job.get("status"), "Error:", job.get("error"))


if __name__ == "__main__":
    main()
