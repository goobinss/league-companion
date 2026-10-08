"""One background worker; the UI consumes messages without doing network I/O."""

from queue import Empty, Full, Queue
import threading
import time

from .config import Settings
from .engine import AutomationEngine
from .lcu import LcuClient, LcuError, find_credentials


class _Cancelled(Exception):
    pass


class _GuardedClient:
    def __init__(self, client, is_current):
        self._client = client
        self._is_current = is_current

    def request(self, method, path, data=None):
        if not self._is_current():
            raise _Cancelled()
        return self._client.request(method, path, data)


class Controller:
    def __init__(self, settings: Settings):
        self.events: Queue = Queue(maxsize=1000)
        self._settings = settings
        self._lock = threading.Lock()
        self._generation = 0
        self._paused = False
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._thread = threading.Thread(target=self._run, name="league-client", daemon=True)
        self._last_status = ""
        self._last_phase = ""

    def _emit(self, kind, payload):
        if self.events.full():
            try:
                self.events.get_nowait()
            except Empty:
                pass
        try:
            self.events.put_nowait((kind, payload))
        except Full:
            pass

    def _status(self, text):
        if text != self._last_status:
            self._last_status = text
            self._emit("status", text)

    def _phase(self, phase):
        if phase != self._last_phase:
            self._last_phase = phase
            self._emit("phase", phase)

    def start(self):
        self._thread.start()

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def stop(self):
        self._stop.set()

    def pause(self, paused: bool):
        with self._lock:
            self._paused = paused
            self._generation += 1

    def update_settings(self, settings: Settings):
        settings.validate()
        with self._lock:
            self._settings = settings
            self._generation += 1

    def refresh_catalog(self):
        self._refresh.set()

    def _current(self, generation):
        with self._lock:
            return not self._stop.is_set() and not self._paused and self._generation == generation

    def _catalog(self, client):
        for kind, path in (("champions", "/lol-game-data/assets/v1/champion-summary.json"),
                           ("runes", "/lol-perks/v1/pages")):
            try:
                items = client.request("GET", path)
                if isinstance(items, list):
                    self._emit(kind, [dict(id=x["id"], name=x["name"]) for x in items
                                      if isinstance(x, dict) and isinstance(x.get("id"), int)
                                      and x["id"] > 0 and isinstance(x.get("name"), str)])
            except LcuError:
                self._emit("log", f"Could not load {kind}; use Refresh after signing in.")

    def _run(self):
        engine = AutomationEngine(lambda text: self._emit("log", text))
        client = credentials = None
        generation_seen = -1
        next_discovery = 0.0
        last_error = ""
        next_error_log = 0.0
        while not self._stop.is_set():
            with self._lock:
                settings, generation, paused = self._settings, self._generation, self._paused
            if generation != generation_seen:
                engine.reset()
                client = credentials = None
                next_discovery = 0
                generation_seen = generation
            try:
                if time.monotonic() >= next_discovery:
                    found = find_credentials(settings.lockfile_path)
                    next_discovery = time.monotonic() + 5
                    if found != credentials:
                        credentials = found
                        client = LcuClient(found) if found else None
                        engine.reset()
                        if client:
                            self._refresh.set()
                if client is None:
                    self._status("Waiting for League client")
                    self._phase("Disconnected")
                else:
                    phase = client.request("GET", "/lol-gameflow/v1/gameflow-phase")
                    if not isinstance(phase, str):
                        raise LcuError("Unexpected gameflow response; waiting for the client.")
                    self._phase(phase)
                    self._status("Paused" if paused else "Connected")
                    if self._refresh.is_set():
                        self._refresh.clear()
                        self._catalog(client)
                    if not paused:
                        engine.step(_GuardedClient(client, lambda: self._current(generation)), settings, phase)
            except _Cancelled:
                pass
            except LcuError as exc:
                message = str(exc)
                if message != last_error or time.monotonic() >= next_error_log:
                    self._emit("log", message)
                    last_error, next_error_log = message, time.monotonic() + 30
                if exc.status in (None, 401, 403):
                    client = credentials = None
                    engine.reset()
                    self._status("Reconnecting to League client")
                    self._phase("Disconnected")
            except Exception as exc:
                # Unexpected client schema/programming errors stop writes. Never
                # stringify arbitrary objects that could contain credentials.
                self.pause(True)
                self._status("Paused after unexpected error")
                self._emit("log", f"Unexpected {type(exc).__name__}. Automations paused; restart after checking settings.")
            self._stop.wait(settings.poll_interval)
