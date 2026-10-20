import struct
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from wgtts.speech.playback import WavePlayer, format_time, parse_time, scale_pcm

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


class TransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent, prefix=".audio-test-"
        )
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "test.wav"
        with wave.open(str(self.path), "wb") as audio:
            audio.setparams((2, 2, 100, 0, "NONE", "not compressed"))
            audio.writeframes(struct.pack("<2000h", *(v for i in range(1000) for v in (i, -i))))
        self.now = 0.0
        self.player = WavePlayer(clock=lambda: self.now)
        patcher = patch("sounddevice.RawOutputStream")
        self.stream_type = patcher.start()
        self.addCleanup(patcher.stop)
        self.stream_type.return_value.latency = 0
        self.addCleanup(self.player.stop)

    def samples(self):
        output = bytearray(8)
        callback = self.stream_type.call_args.kwargs["callback"]
        callback(output, 2, None, None)
        return struct.unpack("<4h", output)

    def test_pause_and_resume_keep_position_and_stereo_frame_alignment(self):
        self.player.start(self.path)
        self.now = 2.0
        self.player.stop()
        self.assertEqual(self.player.position, 2.0)
        self.now = 50.0
        self.assertEqual(self.player.position, 2.0)
        remaining = self.player.start(self.path)
        self.assertAlmostEqual(remaining, 8.05)
        self.assertEqual(self.samples(), (200, -200, 201, -201))

    def test_seek_back_forward_and_absolute_use_recording_without_reloading(self):
        self.player.start(self.path)
        self.now = 5.0
        self.assertEqual(self.player.seek(-2, relative=True), 3.0)
        self.assertIsNone(self.player.stream)
        with patch("wave.open", side_effect=AssertionError("recording reloaded")):
            self.player.start(self.path)
            self.assertEqual(self.samples(), (300, -300, 301, -301))
            self.assertEqual(self.player.seek(2, relative=True), 5.0)
            self.assertEqual(self.player.seek(1.25), 1.25)
            self.player.start(self.path)
        self.assertEqual(self.samples(), (125, -125, 126, -126))

    def test_output_latency_is_not_counted_as_listened_audio(self):
        self.stream_type.return_value.latency = 0.3
        self.player.start(self.path)
        self.now = 0.2
        self.assertEqual(self.player.position, 0.0)
        self.now = 1.3
        self.player.stop()
        self.assertAlmostEqual(self.player.position, 1.0)

    def test_seek_clamps_to_recording_and_play_at_end_starts_from_beginning(self):
        self.player.start(self.path)
        self.assertEqual(self.player.seek(1000), 10.0)
        self.assertEqual(self.player.position, 10.0)
        self.player.start(self.path)
        self.assertEqual(self.samples(), (0, 0, 1, -1))
        self.assertEqual(self.player.seek(-1000, relative=True), 0.0)
        for value in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.player.seek(value)

    def test_new_recording_starts_from_zero(self):
        self.player.start(self.path)
        self.now = 6.0
        other = self.path.with_name("other.wav")
        other.write_bytes(self.path.read_bytes())
        self.player.start(other)
        self.assertEqual(self.player.position, 0.0)
        self.assertEqual(self.samples(), (0, 0, 1, -1))


class TimeTests(unittest.TestCase):
    def test_seconds_minutes_hours_and_relative_offsets(self):
        for value, expected in (
            ("90", (90, False)),
            ("1:30", (90, False)),
            ("1:02:03.5", (3723.5, False)),
            ("+10", (10, True)),
            ("-10", (-10, True)),
            ("-0:30", (-30, True)),
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_time(value), expected)
        self.assertEqual(format_time(90), "1:30")
        self.assertEqual(format_time(3723.5), "1:02:03")

    def test_invalid_positions_are_rejected(self):
        for value in ("nan", "inf", "1:60", "1:2:3:4", "1:", "++10", "--10", "text"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_time(value)


if __name__ == "__main__":
    unittest.main()
