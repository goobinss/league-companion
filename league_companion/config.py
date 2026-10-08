"""Validated, credential-free preferences with atomic persistence."""

from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY", "FILL")


@dataclass(frozen=True)
class Settings:
    auto_accept: bool = True
    accept_delay: float = 1.0
    auto_pick: bool = False
    pick_ids: tuple[int, ...] = ()
    auto_lock: bool = False
    auto_ban: bool = False
    ban_ids: tuple[int, ...] = ()
    action_delay: float = 1.0
    auto_spells: bool = False
    spell1_id: int = 4
    spell2_id: int = 14
    auto_roles: bool = False
    primary_role: str = "MIDDLE"
    secondary_role: str = "TOP"
    auto_reconnect: bool = False
    auto_play_again: bool = False
    queue_timeout: int = 0
    auto_runes: bool = False
    rune_page_id: int = 0
    lockfile_path: str = ""
    poll_interval: float = 1.0

    def validate(self) -> None:
        for field in fields(self):
            if isinstance(field.default, bool) and type(getattr(self, field.name)) is not bool:
                raise ValueError(f"{field.name} must be true or false.")
        for name, lower, upper in (("accept_delay", 0, 8), ("action_delay", 0, 10),
                                   ("poll_interval", 0.5, 5)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name} must be between {lower} and {upper} seconds.")
        for name in ("spell1_id", "spell2_id", "rune_page_id", "queue_timeout"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        if self.queue_timeout > 7200:
            raise ValueError("Queue timeout cannot exceed 7200 seconds.")
        for name in ("pick_ids", "ban_ids"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(values) > 20 or any(type(v) is not int or v <= 0 for v in values):
                raise ValueError(f"{name} must contain up to 20 positive champion IDs.")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contains duplicate champion IDs.")
        if self.primary_role not in ROLES or self.secondary_role not in ROLES:
            raise ValueError("Choose valid role preferences.")
        if self.auto_roles and self.primary_role == self.secondary_role and self.primary_role != "FILL":
            raise ValueError("Choose different primary and secondary roles.")
        if self.auto_pick and not self.pick_ids:
            raise ValueError("Add a champion before enabling automatic picks.")
        if self.auto_lock and not self.auto_pick:
            raise ValueError("Automatic lock-in requires automatic picks.")
        if self.auto_ban and not self.ban_ids:
            raise ValueError("Add a champion before enabling automatic bans.")
        if self.auto_spells and (min(self.spell1_id, self.spell2_id) <= 0 or self.spell1_id == self.spell2_id):
            raise ValueError("Choose two different summoner spells.")
        if self.auto_runes and self.rune_page_id <= 0:
            raise ValueError("Choose an existing rune page.")
        if not isinstance(self.lockfile_path, str):
            raise ValueError("Lockfile path must be text.")


def default_config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "LeagueCompanion" / "settings.json"


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object.")
    unknown = data.keys() - {f.name for f in fields(Settings)}
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    for name in ("pick_ids", "ban_ids"):
        if name in data:
            if not isinstance(data[name], list):
                raise ValueError(f"{name} must be a JSON array.")
            data[name] = tuple(data[name])
    result = Settings(**data)
    result.validate()
    return result


def save_settings(settings: Settings, path: Path) -> None:
    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(settings), handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
