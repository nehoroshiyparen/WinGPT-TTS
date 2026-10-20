"""Visible, time-limited smoke test with the real speech service."""

import argparse
import json

from wgtts.paths import DATA_DIR
from wgtts.settings import Settings
from wgtts.speech.service import SpeechService
from wgtts.ui.widget import SpeechWidget


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--silent", action="store_true", help="Проверить анимацию и кнопки без Applio и звука."
    )
    parser.add_argument("--seconds", type=int, default=180, help="Время показа виджета.")
    args = parser.parse_args()

    class SilentSpeech:
        state = "idle"

        def say(self, text):
            self.state = "playing"

        def stop(self):
            self.state = "idle"

        def close(self):
            self.stop()

    settings = Settings()
    service = SilentSpeech() if args.silent else SpeechService(settings, DATA_DIR / "audio")
    report = {"states": [], "animated_frames": [], "starts": 0, "stops": 0}

    def start():
        report["starts"] += 1
        service.say(
            "Привет. Артас на связи. Озвучка работает. Теперь можно выбирать сообщения в терминале, а кнопка стоп останавливает мой голос."
        )

    def stop():
        report["stops"] += 1
        service.stop()

    window = SpeechWidget(
        settings.appearance.image,
        service,
        start,
        stop,
        lambda: window.root.destroy(),
        size=settings.data["size"],
        appearance=settings.appearance,
        appearance_provider=lambda: settings.appearance,
        presets_provider=settings.presets,
        on_preset=settings.apply_preset,
        on_option=settings.set,
        on_save_preset=settings.save_preset,
    )
    window.root.geometry("+60+80")
    window.root.update_idletasks()
    report["size"] = [window.root.winfo_width(), window.root.winfo_height()]
    report["borderless"] = bool(window.root.overrideredirect())
    print(
        "WIDGET:",
        json.dumps({"size": report["size"], "borderless": report["borderless"]}),
        flush=True,
    )
    frames = set()

    def observe():
        report["final_position"] = [window.root.winfo_x(), window.root.winfo_y()]
        if not report["states"] or report["states"][-1] != service.state:
            report["states"].append(service.state)
            print("STATE:", service.state, flush=True)
        if service.state == "playing":
            frames.add(window.index)
        window.root.after(25, observe)

    window.root.after(25, observe)
    window.root.after(args.seconds * 1000, window.root.destroy)
    try:
        window.root.mainloop()
    finally:
        report["animated_frames"] = sorted(frames)
        report["stopped_frame"] = window.index
        report["final_size"] = window.size
        service.close()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "widget-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
