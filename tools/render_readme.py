"""Render README media from the application's actual portrait and a local WAV."""

import argparse
import math
import os
import shutil
import subprocess
import sys
import wave
from array import array
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from wgtts.ui.portrait import PRESETS, Appearance, Portrait
from wgtts.ui.transport import ACCENT as PLAYER_ACCENT
from wgtts.ui.transport import PANEL_BACKGROUND, DrawerMotion, TransportLayout, _button_art

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs" / "media"
BACKGROUND = "#0c1421"
TEXT = "#edf4ff"
MUTED = "#9aaec5"
ACCENT = "#83d4f5"


@lru_cache(maxsize=32)
def font(size, kind="regular"):
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    names = {
        "regular": ("segoeui.ttf", "DejaVuSans.ttf"),
        "bold": ("seguisb.ttf", "DejaVuSans-Bold.ttf"),
        "mono": ("consola.ttf", "DejaVuSansMono.ttf"),
    }
    native, fallback = names[kind]
    for path in (
        windows / native,
        Path("/usr/share/fonts/truetype/dejavu") / fallback,
    ):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def background(size):
    image = Image.new("RGBA", size, BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((42, 38), "WinGPT TTS", font=font(40, "bold"), fill=TEXT)
    draw.text((42, 104), "Пример озвучки", font=font(26), fill=TEXT)
    draw.text((42, 159), "Запись из data/audio", font=font(18), fill=MUTED)
    draw.text((42, 191), "Анимация портрета из виджета", font=font(18), fill=MUTED)
    draw.line((42, 270, 530, 270), fill="#2a3e56", width=1)
    draw.text((42, 290), "ChatGPT → Applio → воспроизведение", font=font(18), fill=MUTED)
    draw.text((630, 344), "Оформление classic", font=font(14), fill=MUTED)
    return image


def hero_frame(base, portrait, index):
    image = base.copy()
    image.alpha_composite(portrait.render(index, 120), (630, 65))
    return image.convert("RGB")


def render_hero(portrait):
    images = [portrait.render(index, 120).convert("RGB") for index in range(len(portrait.frames))]
    palette = images[0].quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
    frames[0].save(
        MEDIA / "hero.gif",
        save_all=True,
        append_images=frames[1:],
        duration=portrait.durations,
        loop=0,
        disposal=1,
        optimize=True,
    )


def render_presets():
    image = Image.new("RGB", (24 + 274 * len(PRESETS), 350), BACKGROUND)
    draw = ImageDraw.Draw(image)
    descriptions = {
        "modern": "modern / card / compact player",
        "classic": "classic / bevel / 9 px",
        "frost": "frost / bevel / 9 px",
        "minimal": "minimal / flat / 2 px",
        "bare": "bare / no border",
    }
    for column, (name, (label, appearance)) in enumerate(PRESETS.items()):
        x = 24 + column * 274
        draw.rounded_rectangle((x, 20, x + 250, 328), radius=12, fill="#111e2e", outline="#2a3e56")
        draw.text((x + 15, 36), label, font=font(18, "bold"), fill=TEXT)
        draw.text((x + 15, 64), descriptions[name], font=font(12, "mono"), fill=MUTED)
        portrait = Portrait(appearance).render(0, 100)
        position = (x + (250 - portrait.width) // 2, 92 + (221 - portrait.height) // 2)
        image.paste(portrait, position, portrait)
    image.save(MEDIA / "frames.png", optimize=True)


def player_panel(layout, state, name, position=42, duration=130):
    """Use the real transport geometry and button artwork in the documentation."""
    panel = Image.new("RGBA", (layout.width, layout.height), PANEL_BACKGROUND)
    draw = ImageDraw.Draw(panel)
    for action, (x, y) in layout.centers.items():
        glyph = "pause" if action == "play" and state == "playing" else action
        button = _button_art(glyph, layout.diameter(action), "normal")
        panel.alpha_composite(button, (round(x - button.width / 2), round(y - button.height / 2)))
    left, right, y = layout.track
    x = left + (right - left) * position / duration
    draw.line((left, y, right, y), fill="#39414c", width=2)
    draw.line((left, y, x, y), fill=PLAYER_ACCENT, width=2)
    draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#e5f5ff")
    face = font(max(10, round(11 * layout.scale)))
    draw.text(
        (left, y + 13 * layout.scale), f"0:{position:02d}", font=face, fill="#a7afb9", anchor="lm"
    )
    draw.text((right, y + 13 * layout.scale), "2:10", font=face, fill="#a7afb9", anchor="rm")
    row_y = layout.centers["play"][1]
    draw.text((left, row_y), name, font=face, fill="#eef1f6", anchor="lm")
    status = {"playing": "Говорит", "paused": "Пауза"}.get(state, "Готов")
    draw.text((right, row_y), status, font=face, fill="#a7afb9", anchor="rm")
    return panel


def player_card(portrait, elapsed, reveal, state="idle"):
    mode = "talk" if state == "playing" else "idle"
    frame = portrait.render(portrait.frame_at(elapsed, mode), 120, mode)
    layout = TransportLayout.for_portrait((0, 0, frame.width, frame.height))
    pixels = round(layout.height * reveal)
    card = Image.new("RGBA", (frame.width, frame.height + pixels), PANEL_BACKGROUND)
    panel = player_panel(layout, state, portrait.appearance.character_name)
    card.alpha_composite(panel, (0, frame.height - layout.height + pixels))
    card.alpha_composite(frame)
    mask = Image.new("L", (card.width * 4, card.height * 4))
    radius = min(18, round(card.width * 0.06))
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, mask.width - 1, mask.height - 1), radius=radius * 4, fill=255
    )
    card.putalpha(mask.resize(card.size, Image.Resampling.LANCZOS))
    return card


def render_player():
    MEDIA.mkdir(parents=True, exist_ok=True)
    with Portrait(Appearance()) as portrait:
        overview = Image.new("RGBA", (680, 376), BACKGROUND)
        draw = ImageDraw.Draw(overview)
        for x, label, reveal, state in (
            (40, "Ожидание", 0, "idle"),
            (363, "Управление", 1, "playing"),
        ):
            draw.text((x, 24), label, font=font(14), fill=TEXT)
            overview.alpha_composite(player_card(portrait, 0.5, reveal, state), (x, 58))
        overview.convert("RGB").save(MEDIA / "player.png", optimize=True)
        motion = DrawerMotion()
        frames = []
        for number in range(96):
            elapsed = number / 20
            if number == 18:
                motion.to(1, elapsed)
            elif number == 70:
                motion.to(0, elapsed)
            state = "playing" if 1.4 <= elapsed < 3.2 else "paused" if elapsed >= 3.2 else "idle"
            frame = Image.new("RGBA", (345, 334), BACKGROUND)
            frame.alpha_composite(
                player_card(portrait, elapsed, motion.at(elapsed), state), (34, 24)
            )
            frames.append(frame.convert("RGB"))
        frames = [
            frame.quantize(
                colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG
            )
            for frame in frames
        ]
        frames[0].save(
            MEDIA / "player-demo.gif",
            save_all=True,
            append_images=frames[1:],
            duration=50,
            loop=0,
            optimize=True,
            disposal=1,
        )
    print("Rendered player.png and player-demo.gif from the current player artwork and layout.")


def read_voice(path):
    with wave.open(str(path)) as source:
        if source.getsampwidth() != 2:
            raise ValueError("The README sample must be a 16-bit PCM WAV.")
        channels = source.getnchannels()
        rate = source.getframerate()
        values = array("h", source.readframes(source.getnframes()))
    if sys.byteorder != "little":
        values.byteswap()
    mono = [
        sum(values[offset : offset + channels]) / channels
        for offset in range(0, len(values), channels)
    ]
    bars = []
    count = 160
    for index in range(count):
        chunk = mono[index * len(mono) // count : (index + 1) * len(mono) // count]
        bars.append(math.sqrt(sum(value * value for value in chunk) / max(1, len(chunk))))
    peak = max(bars, default=1) or 1
    return len(mono) / rate, [value / peak for value in bars]


def voice_frame(base, portrait, ends, duration, bars, time, poster=False):
    index = min(len(ends) - 1, bisect_right(ends, time % ends[-1]))
    image = hero_frame(base, portrait, index)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((42, 389, 918, 512), radius=13, fill="#132237", outline="#2b4662")
    draw.text((64, 402), "ПРИМЕР ГОЛОСА", font=font(13, "bold"), fill=TEXT)
    draw.text((722, 402), f"{duration:.1f} С / АУДИО", font=font(12, "mono"), fill=MUTED)
    draw.ellipse((65, 441, 109, 485), fill=ACCENT)
    if poster:
        draw.polygon(((83, 452), (83, 474), (99, 463)), fill=BACKGROUND)
    else:
        draw.rectangle((80, 453, 85, 473), fill=BACKGROUND)
        draw.rectangle((90, 453, 95, 473), fill=BACKGROUND)
    progress = max(0, min(1, time / duration))
    for offset, value in enumerate(bars):
        x = 144 + offset * 4.55
        height = 3 + round(value * 21)
        color = "#83d4f5" if offset / len(bars) <= progress else "#365776"
        draw.rounded_rectangle((x, 463 - height, x + 2, 463 + height), radius=1, fill=color)
    draw.text(
        (145, 489),
        f"{int(time) // 60:02}:{int(time) % 60:02} / {int(duration) // 60:02}:{int(duration) % 60:02}",
        font=font(11, "mono"),
        fill=MUTED,
    )
    return image


def render_voice(portrait, source):
    import imageio_ffmpeg

    audio = MEDIA / "voice-sample.wav"
    if source.resolve() != audio.resolve():
        shutil.copyfile(source, audio)
    duration, bars = read_voice(audio)
    ends, elapsed = [], 0
    for milliseconds in portrait.durations:
        elapsed += milliseconds / 1000
        ends.append(elapsed)
    base = background((960, 540))
    voice_frame(base, portrait, ends, duration, bars, 0, poster=True).save(
        MEDIA / "voice-poster.png",
        optimize=True,
    )
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(audio),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-map_metadata",
            "-1",
            str(MEDIA / "voice-sample.mp3"),
        ],
        check=True,
    )
    rate = 25
    process = subprocess.Popen(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "960x540",
            "-r",
            str(rate),
            "-i",
            "pipe:0",
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-map_metadata",
            "-1",
            "-movflags",
            "+faststart",
            "-shortest",
            str(MEDIA / "voice-demo.mp4"),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for number in range(math.ceil(duration * rate)):
            frame = voice_frame(base, portrait, ends, duration, bars, number / rate)
            process.stdin.write(frame.tobytes())
    except BrokenPipeError:
        pass
    finally:
        process.stdin.close()
    error = process.stderr.read().decode("utf-8", errors="replace")
    if process.wait():
        raise RuntimeError(error)
    print(f"Voice sample: {duration:.2f} seconds.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, default=MEDIA / "voice-sample.wav")
    parser.add_argument(
        "--ui-only", action="store_true", help="Обновить только изображения нового плеера."
    )
    args = parser.parse_args()
    if args.ui_only:
        render_player()
        return
    if not args.audio.is_file():
        parser.error("Pass --audio with a WAV sample for the README.")
    MEDIA.mkdir(parents=True, exist_ok=True)
    render_player()
    portrait = Portrait(PRESETS["classic"][1])
    render_hero(portrait)
    render_presets()
    render_voice(portrait, args.audio)
    print(
        "Rendered hero.gif, frames.png, voice-poster.png, voice-sample.wav/mp3 and voice-demo.mp4."
    )


if __name__ == "__main__":
    main()
