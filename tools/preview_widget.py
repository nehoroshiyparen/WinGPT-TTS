"""Visible, time-limited smoke test with the real speech service."""

import argparse
import json
import time

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
    parser.add_argument("--size", type=int, help="Размер для проверки без изменения настроек.")
    args = parser.parse_args()

    class SilentSpeech:
        state = "idle"
        position = 0.0
        duration = 45.0
        started = 0.0

        @property
        def progress(self):
            elapsed = time.monotonic() - self.started if self.state == "playing" else 0
            return min(self.duration, self.position + elapsed), self.duration

        def say(self, text):
            if self.position >= self.duration:
                self.position = 0
            self.started = time.monotonic()
            self.state = "playing"

        def stop(self):
            self.position = self.progress[0]
            self.state = "paused"

        def resume(self):
            if self.state != "paused":
                return False
            self.say("")
            return True

        def seek(self, seconds, *, relative=False):
            playing = self.state == "playing"
            self.stop()
            self.position = max(
                0, min(self.duration, self.position + seconds if relative else seconds)
            )
            if playing:
                self.say("")

        def restart(self):
            self.stop()
            self.position = 0
            self.say("")

        def close(self):
            self.stop()

    settings = Settings()
    service = SilentSpeech() if args.silent else SpeechService(settings, DATA_DIR / "audio")
    report = {"states": [], "animated_frames": [], "starts": 0, "stops": 0}

    def start():
        report["starts"] += 1
        if not service.resume():
            service.say(
                "Привет. Артас на связи. Озвучка работает. Теперь можно выбирать сообщения в терминале, а кнопка паузы запоминает мою позицию."
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
        size=args.size if args.size is not None else settings.data["size"],
        appearance=settings.appearance,
        appearance_provider=lambda: settings.appearance,
        presets_provider=settings.presets,
        on_preset=settings.apply_preset,
        on_option=settings.set,
        on_save_preset=settings.save_preset,
        on_seek=service.seek,
        on_restart=service.restart,
    )
    set_title = window.root.title

    def preview_title(value=None):
        return set_title() if value is None else set_title("Предпросмотр · " + value)

    window.root.title = preview_title
    window.root.title("WinGPT TTS")
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
