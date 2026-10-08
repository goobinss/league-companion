"""Settings validation and persistence boundaries."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from league_companion.config import Settings, load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_defaults_validate_and_missing_file_uses_defaults(self):
        Settings().validate()
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_settings(Path(directory) / "missing.json"), Settings())

    def test_invalid_types_and_nonfinite_values_are_rejected(self):
        cases = {
            "auto_accept": [0, 1, "true", None],
            "auto_lock": ["false", 1],
            "accept_delay": [True, "1", None, -0.1, 8.1],
            "action_delay": [False, "1", -0.1, 10.1],
            "poll_interval": [True, "1", 0, 5.1],
            "spell1_id": [True, 4.0, "4", -1],
            "spell2_id": [False, 14.0, "14", -1],
            "rune_page_id": [True, 1.0, "1", -1],
            "queue_timeout": [True, 1.0, "1", -1, 7201],
            "pick_ids": [[1], "1", (True,), (0,), (-1,), (1.0,), (1, 1), tuple(range(1, 22))],
            "ban_ids": [[1], None, (False,), (0,), ("1",), (1, 1)],
            "primary_role": [None, 1, "mid", "SUPPORT"],
            "secondary_role": [None, [], "INVALID"],
            "lockfile_path": [None, 42, Path("lockfile")],
        }
        for name in ("accept_delay", "action_delay", "poll_interval"):
            cases[name].extend([float("nan"), float("inf"), -float("inf")])
        for name, values in cases.items():
            for value in values:
                with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                    replace(Settings(), **{name: value}).validate()

    def test_enabled_automations_require_complete_configuration(self):
        cases = [
            {"auto_pick": True},
            {"auto_lock": True},
            {"auto_ban": True},
            {"auto_spells": True, "spell1_id": 0},
            {"auto_spells": True, "spell1_id": 4, "spell2_id": 4},
            {"auto_roles": True, "primary_role": "TOP", "secondary_role": "TOP"},
            {"auto_runes": True},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(Settings(), **changes).validate()
        replace(Settings(), auto_roles=True, primary_role="FILL", secondary_role="FILL").validate()

    def test_load_rejects_unknown_keys_and_wrong_json_shapes(self):
        documents = [
            [], None, "settings", 4,
            {"auto_acceppt": True},
            {"pick_ids": "1,2"},
            {"ban_ids": 1},
            {"auto_accept": "false"},
            {"poll_interval": float("nan")},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for document in documents:
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.subTest(document=document), self.assertRaises(ValueError):
                    load_settings(path)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)

    def test_roundtrip_retains_settings_and_serializes_ids_as_arrays(self):
        settings = replace(
            Settings(), auto_pick=True, pick_ids=(103, 7), auto_lock=True,
            auto_ban=True, ban_ids=(238, 157), auto_spells=True,
            auto_runes=True, rune_page_id=1234, queue_timeout=300,
            lockfile_path="C:/Games/League of Legends/lockfile",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "settings.json"
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["pick_ids"], [103, 7])
            self.assertEqual(stored["ban_ids"], [238, 157])
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_replace_preserves_previous_file_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(Settings(), path)
            previous = path.read_bytes()
            with patch("league_companion.config.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    save_settings(replace(Settings(), auto_accept=False), path)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_invalid_settings_never_overwrite_saved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(Settings(), path)
            previous = path.read_bytes()
            with self.assertRaises(ValueError):
                save_settings(replace(Settings(), accept_delay=float("nan")), path)
            self.assertEqual(path.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
