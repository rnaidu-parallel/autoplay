import tempfile
import unittest
from pathlib import Path

from autoplay_harness.recording import GameplayRecorder


class GameplayRecorderTests(unittest.TestCase):
    def test_recording_uses_bounded_segment_ring_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = GameplayRecorder(Path(directory))._command()

        index = command.index("-segment_wrap")
        self.assertEqual("6", command[index + 1])

    def test_zero_retention_keeps_the_full_raw_vod(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = GameplayRecorder(Path(directory), retention_segments=0)._command()

        self.assertNotIn("-segment_wrap", command)

    def test_recording_captures_with_desktop_duplication_and_nvenc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = GameplayRecorder(Path(directory))._command()

        filters = command[command.index("-filter_complex") + 1]
        self.assertIn("ddagrab", filters)
        self.assertIn("output_idx=0", filters)
        self.assertEqual("h264_nvenc", command[command.index("-c:v") + 1])

    def test_fallback_encodes_downloaded_frames_with_libx264(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = GameplayRecorder(Path(directory))._command("libx264")

        filters = command[command.index("-filter_complex") + 1]
        self.assertIn("ddagrab", filters)
        self.assertIn("hwdownload,format=bgra", filters)
        self.assertEqual("libx264", command[command.index("-c:v") + 1])
        self.assertEqual("ultrafast", command[command.index("-preset") + 1])
        self.assertEqual("20", command[command.index("-crf") + 1])


if __name__ == "__main__":
    unittest.main()
