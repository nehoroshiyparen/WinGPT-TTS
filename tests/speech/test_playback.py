import tempfile
import unittest
from array import array
from pathlib import Path

from wgtts.speech.playback import WavePlayer, scale_pcm

WAV = b"RIFF0000WAVEtest"


class VolumeTests(unittest.TestCase):
    def test_half_volume_and_mute_for_signed_pcm(self):
        for width, code in ((2, "h"), (4, "i")):
            pcm = array(code, [-10000, 0, 10000]).tobytes()
            output = array(code)
            output.frombytes(scale_pcm(pcm, width, 50))
            self.assertEqual(list(output), [-5000, 0, 5000])
            self.assertEqual(scale_pcm(pcm, width, 100), pcm)
            self.assertEqual(scale_pcm(pcm, width, 0), bytes(len(pcm)))

    def test_unsigned_pcm_silence_is_centered_at_128(self):
        self.assertEqual(scale_pcm(bytes([0, 128, 255]), 1, 0), bytes([128, 128, 128]))
        self.assertEqual(scale_pcm(bytes([0, 128, 254]), 1, 50), bytes([64, 128, 191]))

    def test_callback_reads_volume_again_for_current_playback(self):
        import struct
        import wave
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".audio-test-"
        ) as directory:
            path = Path(directory) / "test.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(struct.pack("<6h", *([10000] * 6)))
            volume = [100]
            player = WavePlayer(lambda: volume[0])
            with patch("sounddevice.RawOutputStream") as stream_type:
                stream_type.return_value.latency = 0
                player.start(path)
                callback = stream_type.call_args.kwargs["callback"]
                output = bytearray(4)
                callback(output, 2, None, None)
                self.assertEqual(struct.unpack("<2h", output), (10000, 10000))
                volume[0] = 50
                callback(output, 2, None, None)
                self.assertEqual(struct.unpack("<2h", output), (5000, 5000))
                player.stop()
                stream_type.return_value.abort.assert_called_once()
                stream_type.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
