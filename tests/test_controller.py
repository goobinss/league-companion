"""Worker orchestration and CLI safety tests; no League client is needed."""

from dataclasses import replace
import io
from queue import Empty
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from league_companion.__main__ import main
from league_companion.config import Settings
from league_companion.controller import Controller, _Cancelled, _GuardedClient
from league_companion.lcu import Credentials, LcuError


class LoopClock:
    """An Event substitute that advances worker iterations without sleeping."""

    def __init__(self, limit, after_wait=None):
        self.limit = limit
        self.after_wait = after_wait
        self.iterations = 0
        self.now = 0.0
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout):
        self.iterations += 1
        self.now += timeout
        if self.after_wait:
            self.after_wait(self.iterations)
        if self.iterations >= self.limit:
            self.set()
        return self.stopped


def drain(controller):
    result = []
    while True:
        try:
            result.append(controller.events.get_nowait())
        except Empty:
            return result


class ControllerTests(unittest.TestCase):
    def run_worker(self, controller, *, credentials=None, client=None,
                   engine=None, loops=1, after_wait=None, discovery=None):
        clock = LoopClock(loops, after_wait)
        controller._stop = clock
        if client is None:
            client = Mock()
            client.request.return_value = "None"
        if engine is None:
            engine = Mock()
        with patch("league_companion.controller.time.monotonic", side_effect=lambda: clock.now), \
                patch("league_companion.controller.find_credentials", return_value=credentials,
                      side_effect=discovery) as find, \
                patch("league_companion.controller.LcuClient", return_value=client) as create, \
                patch("league_companion.controller.AutomationEngine", return_value=engine):
            controller._run()
        return find, create, engine

    def test_pause_settings_change_and_stop_invalidate_pending_requests(self):
        for change in (lambda worker: worker.pause(True),
                       lambda worker: worker.update_settings(replace(Settings(), auto_accept=False)),
                       lambda worker: worker.stop()):
            with self.subTest(change=change):
                worker = Controller(Settings())
                client = Mock()
                generation = worker._generation
                guarded = _GuardedClient(client, lambda: worker._current(generation))
                guarded.request("GET", "/state")
                change(worker)
                with self.assertRaises(_Cancelled):
                    guarded.request("POST", "/action")
                self.assertEqual(client.request.call_args_list,
                                 [unittest.mock.call("GET", "/state", None)])

    def test_resume_does_not_revive_a_pre_pause_action(self):
        worker = Controller(Settings())
        generation = worker._generation
        worker.pause(True)
        worker.pause(False)
        self.assertFalse(worker._current(generation))
        self.assertTrue(worker._current(worker._generation))

    def test_invalid_settings_do_not_disrupt_active_generation(self):
        worker = Controller(Settings())
        generation = worker._generation
        with self.assertRaises(ValueError):
            worker.update_settings(replace(Settings(), poll_interval=0))
        self.assertTrue(worker._current(generation))

    def test_missing_client_waits_and_throttles_discovery(self):
        worker = Controller(Settings(lockfile_path="chosen-lockfile"))
        find, create, engine = self.run_worker(worker, loops=6)
        self.assertEqual(find.call_count, 2)
        find.assert_called_with("chosen-lockfile")
        create.assert_not_called()
        engine.step.assert_not_called()
        self.assertEqual(drain(worker), [("status", "Waiting for League client"),
                                        ("phase", "Disconnected")])

    def test_pause_keeps_read_only_connection_but_skips_automation(self):
        worker = Controller(Settings())
        worker.pause(True)
        client = Mock()
        client.request.side_effect = ["ReadyCheck", [], []]
        _, _, engine = self.run_worker(worker, credentials=Credentials(1234, "secret"), client=client)
        engine.step.assert_not_called()
        self.assertTrue(all(call.args[0] == "GET" for call in client.request.call_args_list))
        self.assertIn(("status", "Paused"), drain(worker))

    def test_pause_during_action_cancels_the_following_write(self):
        worker = Controller(Settings())
        client = Mock()
        engine = Mock()

        def request(method, path, data=None):
            if path == "/lol-gameflow/v1/gameflow-phase":
                return "ReadyCheck"
            if path == "/action-prerequisite":
                worker.pause(True)
            return []

        def action(guarded, settings, phase):
            guarded.request("GET", "/action-prerequisite")
            guarded.request("POST", "/should-not-run")

        client.request.side_effect = request
        engine.step.side_effect = action
        self.run_worker(worker, credentials=Credentials(1234, "secret"), client=client, engine=engine)
        self.assertFalse(any(call.args[0] == "POST" for call in client.request.call_args_list))
        self.assertTrue(worker.paused)
        self.assertFalse(any("unexpected" in str(payload).lower() for _, payload in drain(worker)))

    def test_rotated_credentials_create_a_fresh_client(self):
        worker = Controller(Settings())
        old, new = Credentials(1234, "old"), Credentials(5678, "new")
        find, create, engine = self.run_worker(worker, loops=6, discovery=[old, new])
        self.assertEqual([call.args[0] for call in create.call_args_list], [old, new])
        self.assertEqual(engine.step.call_count, 6)
        self.assertEqual(find.call_count, 2)

    def test_authentication_failure_reconnects_without_spamming_logs(self):
        worker = Controller(Settings())
        client = Mock()
        client.request.side_effect = LcuError("Authentication failed.", 401)
        _, create, engine = self.run_worker(worker, credentials=Credentials(1234, "secret"),
                                           client=client, loops=11)
        self.assertGreaterEqual(create.call_count, 2)
        engine.step.assert_not_called()
        events = drain(worker)
        self.assertIn(("status", "Reconnecting to League client"), events)
        self.assertEqual(events.count(("log", "Authentication failed.")), 1)

    def test_unexpected_error_pauses_without_exposing_exception_contents(self):
        worker = Controller(Settings())
        client = Mock()
        client.request.side_effect = RuntimeError("credential-that-must-not-appear")
        self.run_worker(worker, credentials=Credentials(1234, "secret"), client=client)
        events = drain(worker)
        self.assertTrue(worker.paused)
        self.assertIn(("status", "Paused after unexpected error"), events)
        self.assertNotIn("credential-that-must-not-appear", repr(events))

    def test_catalog_sanitizes_entries_and_continues_after_one_endpoint_fails(self):
        worker = Controller(Settings())
        client = Mock()
        client.request.side_effect = [LcuError("Not ready.", 404),
                                      [{"id": 7, "name": "My page", "private": "discard"},
                                       {"id": -1, "name": "invalid"}, {"name": "missing"}, None]]
        worker._catalog(client)
        events = drain(worker)
        self.assertIn(("runes", [{"id": 7, "name": "My page"}]), events)
        self.assertEqual(len(events), 2)

    def test_event_backlog_is_bounded_and_keeps_the_latest_message(self):
        worker = Controller(Settings())
        for number in range(1001):
            worker._emit("log", number)
        events = drain(worker)
        self.assertEqual(len(events), 1000)
        self.assertEqual(events[0], ("log", 1))
        self.assertEqual(events[-1], ("log", 1000))

    def test_stopped_worker_does_not_discover_or_connect(self):
        worker = Controller(Settings())
        worker.stop()
        with patch("league_companion.controller.find_credentials") as find:
            worker._run()
        find.assert_not_called()


class EntryPointTests(unittest.TestCase):
    def test_headless_bad_config_fails_before_starting_automation(self):
        with patch.object(sys, "argv", ["league-companion", "--headless"]), \
                patch("league_companion.__main__.load_settings", side_effect=ValueError("Bad config")), \
                patch("league_companion.__main__.Controller") as create, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(main(), 2)
        create.assert_not_called()
        self.assertIn("Could not load settings", stderr.getvalue())

    def test_headless_interrupt_stops_worker_and_only_prints_relevant_events(self):
        worker = Mock()
        worker.events.get.side_effect = [("champions", [{"name": "hidden"}]),
                                         ("status", "Connected"), KeyboardInterrupt]
        with patch.object(sys, "argv", ["league-companion", "--headless", "--paused"]), \
                patch("league_companion.__main__.load_settings", return_value=Settings()), \
                patch("league_companion.__main__.Controller", return_value=worker), \
                patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(main(), 0)
        worker.pause.assert_called_once_with(True)
        worker.start.assert_called_once()
        worker.stop.assert_called_once()
        self.assertIn("[status] Connected", stdout.getvalue())
        self.assertNotIn("hidden", stdout.getvalue())

    def test_gui_bad_config_starts_paused_with_defaults_and_a_visible_warning(self):
        worker = Mock()
        run_ui = Mock()
        with patch.object(sys, "argv", ["league-companion"]), \
                patch("league_companion.__main__.load_settings", side_effect=ValueError("Bad config")), \
                patch("league_companion.__main__.Controller", return_value=worker) as create, \
                patch.dict(sys.modules, {"league_companion.ui": SimpleNamespace(run_ui=run_ui)}):
            self.assertEqual(main(), 0)
        create.assert_called_once_with(Settings())
        worker.pause.assert_called_once_with(True)
        self.assertIn("Started paused", worker.events.put.call_args.args[0][1])
        run_ui.assert_called_once()


if __name__ == "__main__":
    unittest.main()
