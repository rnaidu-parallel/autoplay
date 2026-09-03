import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autoplay_harness.__main__ import main
from autoplay_harness.bridge import BridgeError
from autoplay_harness.openrouter import OpenRouterClient
from autoplay_harness.runner import AutoplayHarness, HarnessError


class AttachTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        with patch("autoplay_harness.runner.ScreenCapture"):
            self.harness = AutoplayHarness(self.root, "Explore town", "location is Town",
                                          OpenRouterClient.LUNA_MODEL, "offline", continuous=True,
                                          launch_game=False, attach=True)
        self.addCleanup(self.harness.director_executor.shutdown)
        self.state = dict(worldReady=True, playerFree=True, menu="none", eventUp=False)
        self.bridge = Mock()
        self.bridge.observe.return_value = {"state": self.state}
        self.bridge.request.side_effect = lambda kind, **kwargs: {
            "status": "completed", "state": {**self.state, "menu": "GameMenu:0:InventoryPage"}}
        self.harness.supervisor = Mock()
        self.harness.supervisor.process = None
        self.harness.supervisor.connect_bridge.return_value = self.bridge
        self.harness._observe = Mock(return_value=(self.state, Mock()))
        self.harness._ensure_world_loaded = Mock()

    def test_failure_pauses_attached_game_without_terminating_it(self):
        self.harness._plan_day_if_needed = Mock(side_effect=RuntimeError("test failure"))
        with patch("autoplay_harness.runner.time.sleep"), self.assertRaisesRegex(RuntimeError, "test failure"):
            self.harness.run()
        self.bridge.request.assert_any_call("press", buttons=["Escape"])
        self.bridge.request.assert_any_call("stop")
        self.harness.supervisor.stop_started_game.assert_not_called()
        self.assertTrue(self.harness.control.status()["closed"])

    def test_attach_refuses_title_screen_without_loading_a_save(self):
        self.state["worldReady"] = False
        with self.assertRaisesRegex(HarnessError, "already loaded game"):
            self.harness.run()
        self.harness._ensure_world_loaded.assert_not_called()
        self.assertFalse(any(call.args[0] == "click" for call in self.bridge.request.call_args_list))
        self.harness.supervisor.stop_started_game.assert_not_called()

    def test_attach_closes_handoff_inventory_before_requesting_a_decision(self):
        self.state['menu'] = 'GameMenu:0:InventoryPage'
        self.bridge.request.side_effect = lambda kind, **kwargs: {
            'status': 'completed', 'state': {**self.state, 'menu': 'none'}}
        def plan(*args):
            self.bridge.request.assert_any_call('press', buttons=['Escape'])
            self.harness.stop_reason = 'test_complete'
        self.harness._plan_day_if_needed = plan
        self.harness._refill_agenda_if_needed = Mock()
        self.harness._finish_director_review = Mock()
        with patch('autoplay_harness.runner.time.sleep'):
            self.harness.run()
        events = self.harness.telemetry.events_path.read_text()
        self.assertIn('handoff_resumed', events)
        self.harness._recover(BridgeError('test reconnect'))
        self.harness.stalled_decisions = 15
        self.harness.last_progress_fingerprint = self.harness._progress_fingerprint(self.state)
        self.harness._update_stall_watchdog('inspect_scene', self.state)
        self.bridge.request.assert_any_call('focus')
        self.assertFalse(any(call.args[0] == 'set_display_mode' for call in self.bridge.request.call_args_list))

    def test_cli_attach_preserves_current_shared_state_without_checkpoint_restore(self):
        state = self.root / "harness" / "state"
        self.harness.notebook.start_day(21)
        self.harness.world.observe({"worldReady": True, "location": "Town", "day": 21})
        before = {path.name: path.read_bytes() for path in state.glob("*.json")}
        with patch("sys.argv", ["autoplay", "run", "--continuous", "--attach", "--budget-usd", "0.2"]), \
                patch("autoplay_harness.__main__.repository_root", return_value=self.root), \
                patch.object(AutoplayHarness, "run", return_value="operator_hold"), \
                patch("autoplay_harness.runner.Checkpoints.restore") as restore, \
                patch("autoplay_harness.runner.ScreenCapture"), \
                patch.dict("os.environ", {"OPENROUTER_API_KEY": "offline"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, main())
        restore.assert_not_called()
        self.assertEqual(before, {path.name: path.read_bytes() for path in state.glob("*.json")})

    def test_cli_rejects_missing_state_and_conflicting_attach_options(self):
        (self.root / "harness" / "state" / "world.json").unlink()
        for extra in ([], ["--resume-checkpoint"], ["--forever"], ["--isolated-state"],
                      ["--objective", "Replace progress", "--success-condition", "location is Farm"]):
            with self.subTest(extra=extra), \
                    patch("sys.argv", ["autoplay", "run", "--attach", *extra]), \
                    patch("autoplay_harness.__main__.repository_root", return_value=self.root), \
                    patch("autoplay_harness.__main__.AutoplayHarness") as build, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(2, error.exception.code)
            build.assert_not_called()
