"""Explicit short end-to-end probe, not run on ordinary startup."""

from threading import Event

from wgtts.paths import DATA_DIR
from wgtts.settings import Settings
from wgtts.speech.applio import ApplioClient


def main():
    settings = Settings()
    client = ApplioClient(settings.data["url"])
    params = client.discover()
    settings.data["tts"] = params
    settings.save()
    print("Model:", params["pthPath"], flush=True)
    print("Voice:", params["ttsVoice"], "Pitch:", params.get("pitch"), flush=True)
    path = client.synthesize(
        "Привет. Артас на связи. Озвучка и виджет готовы к проверке.",
        params,
        DATA_DIR / "audio",
        Event(),
    )
    print(path, flush=True)


if __name__ == "__main__":
    main()
