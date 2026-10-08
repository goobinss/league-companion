"""Small, opt-in client automations. Every write rechecks its live prerequisites."""

from collections.abc import Callable
from dataclasses import dataclass
import math
import time

from .config import Settings
from .lcu import LcuClient, LcuError

PHASE = "/lol-gameflow/v1/gameflow-phase"
SESSION = "/lol-champ-select/v1/session"
READY_CHECK = "/lol-matchmaking/v1/ready-check"
SEARCH = "/lol-matchmaking/v1/search"
LOBBY = "/lol-lobby/v2/lobby"
CURRENT_PAGE = "/lol-perks/v1/currentpage"


@dataclass
class _Attempt:
    due: float
    failures: int = 0
    done: bool = False


class AutomationEngine:
    def __init__(self, log: Callable[[str], None], clock=time.monotonic):
        self.log = log
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        self._phase = ""
        self._attempts: dict[tuple, _Attempt] = {}
        self._owned_selections: dict[tuple, int] = {}
        self._session_identity: tuple | None = None
        self._read_after = 0.0
        self._read_failures = 0

    def step(self, client: LcuClient, settings: Settings, phase: str) -> None:
        if phase != self._phase:
            self.reset()
            self._phase = phase
        if self.clock() < self._read_after:
            return
        try:
            if phase == "ReadyCheck" and settings.auto_accept:
                self._ready_check(client, settings)
            elif phase == "ChampSelect":
                self._champ_select(client, settings)
            elif phase == "Lobby" and settings.auto_roles:
                self._roles(client, settings)
            elif phase == "Matchmaking" and settings.queue_timeout:
                self._queue_timeout(client, settings)
            elif phase == "Reconnect" and settings.auto_reconnect:
                self._run(("reconnect",), settings.action_delay,
                          lambda: self._post_in_phase(client, "Reconnect", "/lol-gameflow/v1/reconnect"))
            elif phase == "EndOfGame" and settings.auto_play_again:
                self._run(("play again",), max(2.0, settings.action_delay),
                          lambda: self._post_in_phase(client, "EndOfGame", "/lol-lobby/v2/play-again"))
            self._read_failures = 0
        except LcuError as exc:
            self._read_failures += 1
            self._read_after = self.clock() + min(2 ** min(self._read_failures, 5), 30)
            if self._read_failures == 1:
                self.log(f"Waiting for client state: {exc}")

    def _run(self, key: tuple, delay: float, action: Callable[[], bool]) -> None:
        attempt = self._attempts.setdefault(key, _Attempt(self.clock() + delay))
        if attempt.done or self.clock() < attempt.due:
            return
        try:
            if action():
                attempt.done = True
            else:
                attempt.due = self.clock() + max(delay, 1.0)
        except LcuError as exc:
            attempt.failures += 1
            attempt.done = attempt.failures >= 5
            attempt.due = self.clock() + min(2 ** attempt.failures, 30)
            suffix = " Disabled for this phase after five failures." if attempt.done else " Will retry."
            self.log(f"Could not {key[0]}: {exc}.{suffix}")

    def _in_phase(self, client: LcuClient, phase: str) -> bool:
        return client.request("GET", PHASE) == phase

    def _post_in_phase(self, client: LcuClient, phase: str, path: str) -> bool:
        if not self._in_phase(client, phase):
            return False
        client.request("POST", path)
        self.log("Reconnecting." if phase == "Reconnect" else "Returned to the lobby.")
        return True

    @staticmethod
    def _pending(check) -> bool:
        return isinstance(check, dict) and check.get("state") == "InProgress" and check.get("playerResponse") == "None"

    def _ready_check(self, client: LcuClient, settings: Settings) -> None:
        if not self._pending(client.request("GET", READY_CHECK)):
            return

        def accept() -> bool:
            if not self._in_phase(client, "ReadyCheck"):
                return False
            if not self._pending(client.request("GET", READY_CHECK)):
                return False
            client.request("POST", READY_CHECK + "/accept")
            self.log("Accepted the match.")
            return True

        self._run(("accept match",), settings.accept_delay, accept)

    @staticmethod
    def _identity(session: dict) -> tuple:
        return session.get("gameId", 0), session.get("localPlayerCellId")

    @staticmethod
    def _same_session(first: tuple, second: tuple) -> bool:
        return first[1] == second[1] and (not first[0] or not second[0] or first[0] == second[0])

    def _fresh_session(self, client: LcuClient) -> dict | None:
        if not self._in_phase(client, "ChampSelect"):
            return None
        session = client.request("GET", SESSION)
        if not isinstance(session, dict) or self._session_identity is None:
            return None
        if not self._same_session(self._session_identity, self._identity(session)):
            return None
        return session

    @staticmethod
    def _actions(session: dict) -> list[dict]:
        return [action for group in session.get("actions", []) if isinstance(group, list)
                for action in group if isinstance(action, dict)]

    @staticmethod
    def _own_active(action: dict, session: dict) -> bool:
        cell = session.get("localPlayerCellId")
        return (cell is not None and action.get("actorCellId") == cell
                and action.get("isInProgress") is True and action.get("completed") is False)

    def _active_action(self, session: dict, action_id: int, kind: str) -> dict | None:
        return next((action for action in self._actions(session)
                     if action.get("id") == action_id and action.get("type") == kind
                     and self._own_active(action, session)), None)

    @staticmethod
    def _ally_choices(session: dict) -> set[int]:
        return {member.get(field) for member in session.get("myTeam", [])
                for field in ("championId", "championPickIntent") if member.get(field)}

    @staticmethod
    def _local_player(session: dict) -> dict | None:
        cell = session.get("localPlayerCellId")
        if type(cell) is not int or cell < 0:
            return None
        return next((member for member in session.get("myTeam", []) if member.get("cellId") == cell), None)

    def _manual_choice(self, session: dict, action: dict, kind: str, ownership: tuple) -> bool:
        owned = self._owned_selections.get(ownership)
        current = action.get("championId", 0)
        local = self._local_player(session)
        intent = local.get("championPickIntent", 0) if local and kind == "pick" else 0
        return bool((current and current != owned) or (intent and intent != owned))

    def _champ_select(self, client: LcuClient, settings: Settings) -> None:
        if not any((settings.auto_pick, settings.auto_ban, settings.auto_spells, settings.auto_runes)):
            return
        session = client.request("GET", SESSION)
        if not isinstance(session, dict):
            return
        identity = self._identity(session)
        if self._session_identity and not self._same_session(self._session_identity, identity):
            self._attempts.clear()
            self._owned_selections.clear()
        if not self._session_identity or identity[0] or identity[1] != self._session_identity[1]:
            self._session_identity = identity
        for action in self._actions(session):
            kind = action.get("type")
            if not self._own_active(action, session) or type(action.get("id")) is not int:
                continue
            if kind == "pick" and settings.auto_pick:
                preferences, lock = settings.pick_ids, settings.auto_lock
            elif kind == "ban" and settings.auto_ban:
                preferences, lock = settings.ban_ids, True
            else:
                continue
            action_id = action["id"]
            key = (kind, action_id, preferences, lock)
            self._run(key, settings.action_delay,
                      lambda: self._draft(client, action_id, kind, preferences, lock))
        if settings.auto_spells:
            self._run(("set spells", settings.spell1_id, settings.spell2_id), settings.action_delay,
                      lambda: self._spells(client, settings))
        if settings.auto_runes:
            self._run(("select rune page", settings.rune_page_id), settings.action_delay,
                      lambda: self._runes(client, settings.rune_page_id))

    def _draft(self, client: LcuClient, action_id: int, kind: str,
               preferences: tuple[int, ...], lock: bool) -> bool:
        session = self._fresh_session(client)
        action = self._active_action(session, action_id, kind) if session else None
        if action is None:
            return False
        ownership = (kind, action_id)
        current = action.get("championId", 0)
        if self._manual_choice(session, action, kind, ownership):
            return True  # Preserve manual hovers, including their lock-in decision.
        chosen = current
        if not chosen or (kind == "ban" and chosen in self._ally_choices(session)):
            endpoint = "pickable-champion-ids" if kind == "pick" else "bannable-champion-ids"
            available = client.request("GET", "/lol-champ-select/v1/" + endpoint)
            if not isinstance(available, list):
                return False
            # Availability reads can race a manual choice or the end of a turn.
            session = self._fresh_session(client)
            action = self._active_action(session, action_id, kind) if session else None
            if action is None:
                return False
            current = action.get("championId", 0)
            if self._manual_choice(session, action, kind, ownership):
                return True
            excluded = self._ally_choices(session) if kind == "ban" else set()
            chosen = next((champion for champion in preferences
                           if champion in available and champion not in excluded), None)
            if chosen is None:
                return False
            client.request("PATCH", f"{SESSION}/actions/{action_id}", {"championId": chosen})
            self._owned_selections[ownership] = chosen
            self.log(f"Selected champion {chosen} to {kind}.")
        if lock:
            session = self._fresh_session(client)
            action = self._active_action(session, action_id, kind) if session else None
            if (action is None or action.get("championId") != chosen
                    or self._manual_choice(session, action, kind, ownership)):
                return False
            if kind == "ban" and chosen in self._ally_choices(session):
                return False
            client.request("POST", f"{SESSION}/actions/{action_id}/complete")
            self.log(f"Confirmed {kind}: champion {chosen}.")
        return True

    def _spells(self, client: LcuClient, settings: Settings) -> bool:
        session = self._fresh_session(client)
        if session is None:
            return False
        local = self._local_player(session)
        if local is None:
            return False
        if (local.get("spell1Id"), local.get("spell2Id")) != (settings.spell1_id, settings.spell2_id):
            client.request("PATCH", SESSION + "/my-selection",
                           {"spell1Id": settings.spell1_id, "spell2Id": settings.spell2_id})
            self.log("Applied summoner spells.")
        return True

    def _runes(self, client: LcuClient, page_id: int) -> bool:
        current = client.request("GET", CURRENT_PAGE)
        if self._fresh_session(client) is None:
            return False
        if isinstance(current, dict) and current.get("id") == page_id:
            return True
        client.request("PUT", CURRENT_PAGE, page_id)
        self.log(f"Selected rune page {page_id}.")
        return True

    def _roles(self, client: LcuClient, settings: Settings) -> None:
        def select() -> bool:
            if not self._in_phase(client, "Lobby"):
                return False
            lobby = client.request("GET", LOBBY)
            if not isinstance(lobby, dict) or not lobby.get("gameConfig", {}).get("showPositionSelector"):
                return False
            member = lobby.get("localMember", {})
            wanted = (settings.primary_role, settings.secondary_role)
            if (member.get("firstPositionPreference"), member.get("secondPositionPreference")) != wanted:
                client.request("PUT", LOBBY + "/members/localMember/position-preferences",
                               {"firstPreference": wanted[0], "secondPreference": wanted[1]})
                self.log(f"Applied roles: {wanted[0]} / {wanted[1]}.")
            return True

        self._run(("set roles", settings.primary_role, settings.secondary_role), settings.action_delay, select)

    def _queue_timeout(self, client: LcuClient, settings: Settings) -> None:
        def cancel() -> bool:
            if not self._in_phase(client, "Matchmaking"):
                return False
            search = client.request("GET", SEARCH)
            if not isinstance(search, dict) or search.get("searchState") != "Searching":
                return False
            elapsed = search.get("timeInQueue")
            if (type(elapsed) not in (int, float) or not math.isfinite(elapsed)
                    or elapsed < settings.queue_timeout):
                return False
            client.request("DELETE", LOBBY + "/matchmaking/search")
            self.log(f"Stopped queue after {settings.queue_timeout} seconds.")
            return True

        self._run(("stop queue", settings.queue_timeout), 0, cancel)
