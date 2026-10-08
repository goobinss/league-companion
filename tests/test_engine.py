"""Behavioral checks for automation guards, races, and retry limits."""

from copy import deepcopy
from dataclasses import replace
import unittest

from league_companion.config import Settings
from league_companion.engine import AutomationEngine, CURRENT_PAGE, LOBBY, PHASE, READY_CHECK, SEARCH, SESSION
from league_companion.lcu import LcuError


class FakeClock:
    now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds=1):
        self.now += seconds


class FakeClient:
    def __init__(self):
        self.phase = "ChampSelect"
        self.check = {"state": "InProgress", "playerResponse": "None"}
        self.session = {"gameId": 100, "localPlayerCellId": 1,
                        "myTeam": [{"cellId": 1, "championId": 0, "spell1Id": 4, "spell2Id": 7}],
                        "actions": [[{"id": 7, "actorCellId": 1, "type": "pick",
                                      "isInProgress": True, "completed": False, "championId": 0}]]}
        self.available = [1, 2, 3]
        self.page = {"id": 1}
        self.lobby = {"gameConfig": {"showPositionSelector": True}, "localMember": {}}
        self.search = {"searchState": "Searching", "timeInQueue": 0.0}
        self.calls = []
        self.overrides = {}

    @property
    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]

    @property
    def action(self):
        return self.session["actions"][0][0]

    def request(self, method, path, data=None):
        self.calls.append((method, path, deepcopy(data)))
        if (method, path) in self.overrides:
            return self.overrides[method, path](data)
        if method == "GET":
            endpoints = {PHASE: self.phase, SESSION: self.session, READY_CHECK: self.check,
                         CURRENT_PAGE: self.page, LOBBY: self.lobby, SEARCH: self.search,
                         "/lol-champ-select/v1/pickable-champion-ids": self.available,
                         "/lol-champ-select/v1/bannable-champion-ids": self.available}
            return deepcopy(endpoints[path])
        if method == "PATCH" and path == f"{SESSION}/actions/7":
            self.action.update(data)
        elif method == "POST" and path == f"{SESSION}/actions/7/complete":
            self.action["completed"] = True
        elif method == "PATCH" and path == SESSION + "/my-selection":
            self.session["myTeam"][0].update(data)
        elif method == "PUT" and path == CURRENT_PAGE:
            self.page["id"] = data
        elif method == "POST" and path == READY_CHECK + "/accept":
            self.check["playerResponse"] = "Accepted"
        return None


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.messages = []
        self.engine = AutomationEngine(self.messages.append, self.clock)
        self.client = FakeClient()
        self.settings = Settings(auto_accept=False, action_delay=0)

    def step(self, phase=None, **settings):
        if phase is not None:
            self.client.phase = phase
        self.engine.step(self.client, replace(self.settings, **settings), self.client.phase)

    def test_accept_waits_then_only_accepts_once(self):
        self.step("ReadyCheck", auto_accept=True, accept_delay=2)
        self.assertEqual(self.client.writes, [])
        self.clock.advance(1)
        self.step(auto_accept=True, accept_delay=2)
        self.assertEqual(self.client.writes, [])
        self.clock.advance(1)
        self.step(auto_accept=True, accept_delay=2)
        self.clock.advance(10)
        self.step(auto_accept=True, accept_delay=2)
        self.assertEqual(self.client.writes, [("POST", READY_CHECK + "/accept", None)])

    def test_accept_rechecks_manual_response(self):
        reads = 0

        def check(_):
            nonlocal reads
            reads += 1
            return {"state": "InProgress", "playerResponse": "None" if reads == 1 else "Accepted"}

        self.client.overrides["GET", READY_CHECK] = check
        self.step("ReadyCheck", auto_accept=True, accept_delay=0)
        self.assertEqual(self.client.writes, [])

    def test_accept_rechecks_phase(self):
        self.client.overrides["GET", PHASE] = lambda _: "ChampSelect"
        self.step("ReadyCheck", auto_accept=True, accept_delay=0)
        self.assertEqual(self.client.writes, [])

    def test_new_ready_check_after_phase_transition_is_accepted(self):
        self.step("ReadyCheck", auto_accept=True, accept_delay=0)
        self.step("Matchmaking", auto_accept=True, accept_delay=0)
        self.client.check["playerResponse"] = "None"
        self.step("ReadyCheck", auto_accept=True, accept_delay=0)
        self.assertEqual(len(self.client.writes), 2)

    def test_pick_uses_available_priority_without_implicit_lock(self):
        self.client.available = [2, 3]
        for _ in range(5):
            self.step(auto_pick=True, pick_ids=(1, 2, 3))
            self.clock.advance()
        self.assertEqual(self.client.writes, [("PATCH", f"{SESSION}/actions/7", {"championId": 2})])

    def test_only_own_active_incomplete_turn_can_be_picked(self):
        for field, value in (("actorCellId", 2), ("isInProgress", False), ("completed", True)):
            with self.subTest(field=field):
                original = self.client.action[field]
                self.client.action[field] = value
                self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
                self.client.action[field] = original
        self.assertEqual(self.client.writes, [])

    def test_manual_hover_is_not_overwritten_or_locked(self):
        self.client.action["championId"] = 3
        self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
        self.assertEqual(self.client.writes, [])

    def test_manual_planning_intent_is_preserved_when_pick_turn_begins(self):
        self.client.session["myTeam"][0]["championPickIntent"] = 3
        self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
        self.assertEqual(self.client.writes, [])

    def test_pick_locks_only_after_fresh_selection_check(self):
        self.step(auto_pick=True, pick_ids=(2,), auto_lock=True)
        self.step(auto_pick=True, pick_ids=(2,), auto_lock=True)
        self.assertEqual(self.client.writes, [("PATCH", f"{SESSION}/actions/7", {"championId": 2}),
                                             ("POST", f"{SESSION}/actions/7/complete", None)])

    def test_manual_change_between_hover_and_lock_is_preserved(self):
        def manual_change(data):
            self.client.action["championId"] = 3

        self.client.overrides["PATCH", f"{SESSION}/actions/7"] = manual_change
        self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
        self.clock.advance()
        self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
        self.assertEqual(len(self.client.writes), 1)
        self.assertEqual(self.client.action["championId"], 3)

    def test_availability_read_cannot_carry_write_into_inactive_turn(self):
        def close_turn(_):
            self.client.action["isInProgress"] = False
            return [1]

        self.client.overrides["GET", "/lol-champ-select/v1/pickable-champion-ids"] = close_turn
        self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
        self.assertEqual(self.client.writes, [])

    def test_changed_session_cannot_receive_old_scheduled_action(self):
        reads = 0

        def session(_):
            nonlocal reads
            reads += 1
            result = deepcopy(self.client.session)
            result["gameId"] = 100 if reads == 1 else 200
            return result

        self.client.overrides["GET", SESSION] = session
        self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
        self.assertEqual(self.client.writes, [])

    def test_ban_excludes_allies_picks_and_intents(self):
        self.client.action["type"] = "ban"
        self.client.session["myTeam"].extend([{"cellId": 2, "championId": 1},
                                                {"cellId": 3, "championPickIntent": 2}])
        self.step(auto_ban=True, ban_ids=(1, 2, 3))
        self.assertEqual(self.client.writes, [("PATCH", f"{SESSION}/actions/7", {"championId": 3}),
                                             ("POST", f"{SESSION}/actions/7/complete", None)])

    def test_ban_does_nothing_when_all_priorities_are_ally_hovers(self):
        self.client.action["type"] = "ban"
        self.client.session["myTeam"].append({"cellId": 2, "championPickIntent": 1})
        self.step(auto_ban=True, ban_ids=(1,))
        self.assertEqual(self.client.writes, [])

    def test_spells_apply_once_and_preserve_later_manual_change(self):
        self.step(auto_spells=True, spell1_id=4, spell2_id=14)
        self.client.session["myTeam"][0]["spell2Id"] = 7
        self.step(auto_spells=True, spell1_id=4, spell2_id=14)
        self.assertEqual(self.client.writes, [("PATCH", SESSION + "/my-selection", {"spell1Id": 4, "spell2Id": 14})])
        self.step("Lobby")
        self.step("ChampSelect", auto_spells=True, spell1_id=4, spell2_id=14)
        self.assertEqual(len(self.client.writes), 2)

    def test_matching_spells_and_rune_page_do_not_write(self):
        self.step(auto_spells=True, spell1_id=4, spell2_id=7, auto_runes=True, rune_page_id=1)
        self.assertEqual(self.client.writes, [])

    def test_rune_selection_uses_integer_and_happens_once(self):
        self.step(auto_runes=True, rune_page_id=42)
        self.client.page["id"] = 1
        self.step(auto_runes=True, rune_page_id=42)
        self.assertEqual(self.client.writes, [("PUT", CURRENT_PAGE, 42)])

    def test_roles_only_apply_in_lobbies_with_position_selection(self):
        self.client.lobby["gameConfig"]["showPositionSelector"] = False
        self.step("Lobby", auto_roles=True)
        self.assertEqual(self.client.writes, [])
        self.client.lobby["gameConfig"]["showPositionSelector"] = True
        self.clock.advance()
        self.step(auto_roles=True)
        self.step(auto_roles=True)
        self.assertEqual(self.client.writes, [("PUT", LOBBY + "/members/localMember/position-preferences",
                                              {"firstPreference": "MIDDLE", "secondPreference": "TOP"})])

    def test_queue_timeout_waits_and_cancels_once(self):
        self.client.search["timeInQueue"] = 59.9
        self.step("Matchmaking", queue_timeout=60)
        self.assertEqual(self.client.writes, [])
        self.client.search["timeInQueue"] = 60.0
        self.clock.advance()
        self.step(queue_timeout=60)
        self.clock.advance()
        self.step(queue_timeout=60)
        self.assertEqual(self.client.writes, [("DELETE", LOBBY + "/matchmaking/search", None)])

    def test_timeout_never_cancels_found_match(self):
        self.client.search.update(timeInQueue=90, searchState="Found")
        self.step("Matchmaking", queue_timeout=60)
        self.assertEqual(self.client.writes, [])

    def test_timeout_rejects_nonfinite_client_timers(self):
        for elapsed in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(elapsed=elapsed):
                self.client.search["timeInQueue"] = elapsed
                self.step("Matchmaking", queue_timeout=60)
                self.clock.advance()
        self.assertEqual(self.client.writes, [])

    def test_failed_lock_retries_without_repeating_successful_hover(self):
        failures = 0

        def fail_once(_):
            nonlocal failures
            failures += 1
            if failures == 1:
                raise LcuError("Temporary failure", 500)
            self.client.action["completed"] = True

        self.client.overrides["POST", f"{SESSION}/actions/7/complete"] = fail_once
        self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
        self.clock.advance()
        self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
        self.assertEqual(failures, 1)
        self.clock.advance()
        self.step(auto_pick=True, pick_ids=(1,), auto_lock=True)
        self.assertEqual(failures, 2)
        self.assertEqual(sum(call[0] == "PATCH" for call in self.client.writes), 1)

    def test_retries_are_bounded_even_if_client_keeps_failing(self):
        def fail(_):
            raise LcuError("Unavailable", 500)

        self.client.overrides["POST", READY_CHECK + "/accept"] = fail
        for _ in range(20):
            self.step("ReadyCheck", auto_accept=True, accept_delay=0)
            self.clock.advance(31)
        self.assertEqual(len(self.client.writes), 5)
        self.assertIn("five failures", self.messages[-1])

    def test_failed_state_reads_back_off(self):
        def fail(_):
            raise LcuError("Unavailable", 500)

        self.client.overrides["GET", SESSION] = fail
        self.step(auto_pick=True, pick_ids=(1,))
        self.clock.advance()
        self.step(auto_pick=True, pick_ids=(1,))
        self.assertEqual(len(self.client.calls), 1)
        self.clock.advance()
        self.step(auto_pick=True, pick_ids=(1,))
        self.assertEqual(len(self.client.calls), 2)

    def test_play_again_waits_and_never_starts_queue(self):
        self.step("EndOfGame", auto_play_again=True)
        self.assertEqual(self.client.writes, [])
        self.clock.advance(2)
        self.step(auto_play_again=True)
        self.step(auto_play_again=True)
        self.assertEqual(self.client.writes, [("POST", "/lol-lobby/v2/play-again", None)])

    def test_reconnect_only_once_per_phase(self):
        for _ in range(3):
            self.step("Reconnect", auto_reconnect=True)
        self.assertEqual(self.client.writes, [("POST", "/lol-gameflow/v1/reconnect", None)])

    def test_no_automation_runs_during_gameplay_or_when_disabled(self):
        self.step()
        self.assertEqual(self.client.calls, [])
        self.step("InProgress", auto_accept=True, auto_pick=True, pick_ids=(1,),
                  auto_ban=True, ban_ids=(2,), auto_lock=True, auto_spells=True,
                  auto_runes=True, rune_page_id=42, auto_roles=True,
                  auto_reconnect=True, auto_play_again=True, queue_timeout=60)
        self.assertEqual(self.client.calls, [])


if __name__ == "__main__":
    unittest.main()
