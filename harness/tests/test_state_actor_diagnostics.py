import unittest

from autoplay_harness.state_actor import compact_state


class StateActorDiagnosticsTests(unittest.TestCase):
    def test_compact_state_drops_diagnostics(self):
        compact = compact_state({"worldReady": True, "diagnostics": {"isInBed": True}})

        self.assertNotIn("diagnostics", compact)
