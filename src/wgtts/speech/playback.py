"""PCM volume control and interruptible local WAV playback."""

import wave
from array import array


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
    def __init__(self, volume_provider=lambda: 100):
        self.volume_provider = volume_provider
        self.stream = None

    def start(self, path):
        import sounddevice as sd

        self.stop()
        with wave.open(str(path), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            width, channels, rate = audio.getsampwidth(), audio.getnchannels(), audio.getframerate()
            pcm = audio.readframes(audio.getnframes())
        offset = 0

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
            self.stream.start()
        except Exception:
            self.stop()
            raise
        return duration + self.stream.latency + 0.05

    def stop(self):
        if self.stream is not None:
            self.stream.abort(ignore_errors=True)
            self.stream.close(ignore_errors=True)
            self.stream = None
