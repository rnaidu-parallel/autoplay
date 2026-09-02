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


if __name__ == "__main__":
    unittest.main()
