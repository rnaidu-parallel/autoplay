import copy
import unittest

from autoplay_harness.farming import check_mail


class MailBridge:
    def __init__(self, opens=True):
        self.opens = opens
        self.calls = []
        self.state = dict(worldReady=True, playerFree=True, canMove=True, location="Farm",
                          menu="none", health=100, eventUp=False, mailCount=2,
                          tileX=1, tileY=1, facing=0,
                          mailboxTile=dict(x=3, y=2, screenX=900, screenY=500),
                          navigationOriginX=0, navigationOriginY=0, navigationRows=["....."] * 5)

    def request(self, kind, **args):
        self.calls.append((kind, args))
        if kind == "navigate":
            self.state.update(tileX=args["x"], tileY=args["y"])
            self.state["mailboxTile"].update(screenX=500, screenY=300)
        elif kind == "press":
            self.state["facing"] = {"W": 0, "D": 1, "S": 2, "A": 3}[args["buttons"][0]]
        elif kind == "click":
            if self.opens:
                self.state.update(menu="LetterViewerMenu", mailCount=1)
        return {"status": "completed", "state": copy.deepcopy(self.state)}


class MailTests(unittest.TestCase):
    def test_mail_aims_at_target_after_navigation_changes_the_viewport(self):
        bridge = MailBridge()
        result = check_mail(bridge, copy.deepcopy(bridge.state))
        self.assertEqual("completed", result["status"])
        self.assertEqual(1, result["state"]["mailCount"])
        self.assertEqual(("click", dict(x=500, y=300, button="right")), bridge.calls[-1])
        self.assertNotIn(("press", {"buttons": ["X"]}), bridge.calls)

    def test_completed_input_without_a_letter_is_still_blocked(self):
        bridge = MailBridge(opens=False)
        result = check_mail(bridge, copy.deepcopy(bridge.state))
        self.assertEqual("blocked", result["status"])
        self.assertEqual("letter_did_not_open", result["reason"])
        self.assertEqual(1, sum(kind == "click" for kind, _ in bridge.calls))

    def test_mail_is_not_attempted_from_inside_the_house(self):
        bridge = MailBridge()
        result = check_mail(bridge, {**bridge.state, "location": "FarmHouse"})
        self.assertEqual("rejected", result["status"])
        self.assertEqual([], bridge.calls)

    def test_missing_fresh_target_never_uses_old_screen_coordinates(self):
        bridge = MailBridge()
        request = bridge.request

        def lose_target(kind, **args):
            result = request(kind, **args)
            if kind == "navigate":
                bridge.state["mailboxTile"].pop("screenX")
                result["state"]["mailboxTile"].pop("screenX")
            return result

        bridge.request = lose_target
        result = check_mail(bridge, copy.deepcopy(bridge.state))
        self.assertEqual("mailbox_target_unavailable", result["reason"])
        self.assertFalse(any(kind == "click" for kind, _ in bridge.calls))


if __name__ == "__main__":
    unittest.main()
