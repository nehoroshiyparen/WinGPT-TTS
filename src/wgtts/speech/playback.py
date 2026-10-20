"""PCM volume control and interruptible local WAV playback."""

import math
import time
import wave
from array import array
from pathlib import Path
from threading import RLock


def format_time(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def parse_time(value):
    """Seconds, M:SS or H:MM:SS; a leading sign means a relative seek."""
    relative = value.startswith(("+", "-"))
    sign = -1 if value.startswith("-") else 1
    body = value[1:] if relative else value
    try:
        parts = body.split(":")
        if not 1 <= len(parts) <= 3 or not all(parts) or body.startswith(("+", "-")):
            raise ValueError
        numbers = [float(part) for part in parts]
        if any(not math.isfinite(part) or part < 0 for part in numbers):
            raise ValueError
        if len(parts) > 1 and (
            any(not part.isdigit() for part in parts[:-1])
            or any(part >= 60 for part in numbers[1:])
        ):
            raise ValueError
        seconds = 0
        for number in numbers:
            seconds = seconds * 60 + number
        return sign * seconds, relative
    except ValueError:
        raise ValueError("Используй: seek +10, seek -10, seek 90 или seek 1:30.") from None


def scale_pcm(data, width, volume):
    volume = max(0, min(100, volume))
    if volume == 100:
        return data
    if volume == 0:
        return (b"\x80" if width == 1 else b"\x00") * len(data)
    gain = volume / 100
    if width == 1:
        return bytes(round((value - 128) * gain) + 128 for value in data)
    if width in (2, 4):
        samples = array("h" if width == 2 else "i")
        samples.frombytes(data)
        return array(samples.typecode, (round(value * gain) for value in samples)).tobytes()
    if width == 3:
        return b"".join(
            round(int.from_bytes(data[i : i + 3], "little", signed=True) * gain).to_bytes(
                3, "little", signed=True
            )
            for i in range(0, len(data), 3)
        )
    raise ValueError("Неподдерживаемый формат PCM.")


class WavePlayer:
    def __init__(self, volume_provider=lambda: 100, *, clock=time.monotonic):
        self.volume_provider = volume_provider
        self.clock = clock
        self.lock = RLock()
        self.stream = None
        self.path = None
        self.duration = 0.0
        self._position = 0.0
        self._started_at = 0.0
        self._latency = 0.0
        self._pcm = b""

    @property
    def position(self):
        with self.lock:
            elapsed = (
                max(0.0, self.clock() - self._started_at - self._latency)
                if self.stream is not None
                else 0.0
            )
            return min(self.duration, self._position + elapsed)

    def start(self, path):
        with self.lock:
            return self._start(Path(path))

    def _start(self, path):
        import sounddevice as sd

        self.stop()
        if path != self.path:
            with wave.open(str(path), "rb") as audio:
                width = audio.getsampwidth()
                channels, rate = audio.getnchannels(), audio.getframerate()
                pcm = audio.readframes(audio.getnframes())
                duration = audio.getnframes() / rate
            self.path, self.duration, self._position = path, duration, 0.0
            self._pcm, self._format = pcm, (width, channels, rate)
        width, channels, rate = self._format
        pcm = self._pcm
        if self._position >= self.duration:
            self._position = 0.0
        # Resume at an interleaved frame boundary, never halfway through a sample.
        frame = int(self._position * rate)
        self._position = frame / rate
        offset = frame * width * channels

        def callback(output, frames, timing, status):
            nonlocal offset
            chunk = pcm[offset : offset + len(output)]
            offset += len(chunk)
            # Consult live settings every audio block, including queued speech.
            chunk = scale_pcm(chunk, width, self.volume_provider())
            silence = b"\x80" if width == 1 else b"\x00"
            output[:] = chunk + silence * (len(output) - len(chunk))
            if offset >= len(pcm):
                raise sd.CallbackStop

        self.stream = sd.RawOutputStream(
            samplerate=rate,
            channels=channels,
            dtype={1: "uint8", 2: "int16", 3: "int24", 4: "int32"}[width],
            blocksize=1024,
            callback=callback,
        )
        try:
            self._latency = self.stream.latency
            self._started_at = self.clock()
            self.stream.start()
        except Exception:
            self.stop()
            raise
        return self.duration - self._position + self._latency + 0.05

    def stop(self):
        with self.lock:
            if self.stream is not None:
                self._position = self.position
                self.stream.abort(ignore_errors=True)
                self.stream.close(ignore_errors=True)
                self.stream = None

    def seek(self, seconds, *, relative=False):
        if not math.isfinite(seconds):
            raise ValueError("Позиция должна быть конечным числом секунд.")
        with self.lock:
            self.stop()
            target = self._position + seconds if relative else seconds
            self._position = max(0.0, min(self.duration, target))
            return self._position
