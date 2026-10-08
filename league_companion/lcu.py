"""LCU discovery and a loopback-only HTTPS transport. No public API key needed."""

import base64
from dataclasses import dataclass, field
import json
from pathlib import Path
import ssl
import subprocess
import sys
import urllib.error
import urllib.request


class LcuError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Credentials:
    port: int
    password: str = field(repr=False)
    pid: int = 0


def read_lockfile(path: Path) -> Credentials:
    parts = path.read_text(encoding="utf-8").strip().split(":")
    if len(parts) != 5 or parts[4] != "https":
        raise ValueError("Invalid League lockfile.")
    _, raw_pid, raw_port, password, _ = parts
    pid, port = int(raw_pid), int(raw_port)
    if pid <= 0 or not 1 <= port <= 65535 or not password:
        raise ValueError("Invalid League lockfile values.")
    return Credentials(port, password, pid)


def _process_lockfiles() -> list[Path]:
    """Find nonstandard installs using executable paths; never print command lines."""
    if sys.platform == "win32":
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   "Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\" "
                   "| Select-Object -ExpandProperty ExecutablePath | ConvertTo-Json -Compress"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    elif sys.platform == "darwin":
        command = ["/bin/ps", "-axo", "comm="]
        flags = 0
    else:
        return []
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3,
                                check=True, creationflags=flags)
        if sys.platform == "win32":
            paths = json.loads(result.stdout or "[]")
            paths = [paths] if isinstance(paths, str) else paths
            return [Path(p).parent / "lockfile" for p in (paths or []) if isinstance(p, str)]
        paths = [Path(p.strip()) for p in result.stdout.splitlines() if "LeagueClientUx" in p]
        return [parent / "lockfile" for p in paths for parent in list(p.parents)[:5]]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def find_credentials(lockfile_path: str = "") -> Credentials | None:
    if lockfile_path:
        candidates = [Path(lockfile_path).expanduser()]
    else:
        candidates = [Path("C:/Riot Games/League of Legends/lockfile"),
                      Path("D:/Riot Games/League of Legends/lockfile"),
                      Path("/Applications/League of Legends.app/Contents/LoL/lockfile"),
                      Path.home() / "Applications/League of Legends.app/Contents/LoL/lockfile"]
    for path in candidates:
        try:
            return read_lockfile(path)
        except (OSError, ValueError):
            continue
    if not lockfile_path:
        for path in _process_lockfiles():
            try:
                return read_lockfile(path)
            except (OSError, ValueError):
                continue
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LcuClient:
    def __init__(self, credentials: Credentials):
        if not 1 <= credentials.port <= 65535:
            raise ValueError("Invalid local client port.")
        self._base = f"https://127.0.0.1:{credentials.port}"
        self._authorization = "Basic " + base64.b64encode(
            f"riot:{credentials.password}".encode()).decode()
        # LCU uses a self-signed certificate. This context is restricted to the
        # literal loopback host; redirects and environment proxies are disabled.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
            urllib.request.HTTPSHandler(context=context))

    def request(self, method: str, path: str, data=None):
        if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\r\n\\?#"):
            raise ValueError("Use an absolute local API path without a query or fragment.")
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise ValueError("Unsupported HTTP method.")
        body = json.dumps(data, allow_nan=False).encode() if data is not None else None
        request = urllib.request.Request(self._base + path, data=body, method=method,
                                         headers={"Authorization": self._authorization,
                                                  "Content-Type": "application/json",
                                                  "Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=2) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise LcuError("The client response was too large.")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise LcuError(f"LCU returned HTTP {status} for {method} {path}.", status) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise LcuError("The local League client is unavailable.") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise LcuError("The local client returned an unexpected response.") from None
