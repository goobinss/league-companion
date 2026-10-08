"""GUI entry point and optional headless runner."""

import argparse
from pathlib import Path
from queue import Empty
import sys

from .config import default_config_path, load_settings, Settings
from .controller import Controller


def main() -> int:
    parser = argparse.ArgumentParser(description="Local League client automations.")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--headless", action="store_true", help="Run with saved settings and console logs")
    parser.add_argument("--paused", action="store_true", help="Start without performing actions")
    args = parser.parse_args()
    warning = ""
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError, TypeError) as exc:
        if args.headless:
            print(f"Could not load settings: {exc}", file=sys.stderr)
            return 2
        warning = f"Could not load settings: {exc}. Started paused with defaults; your file was not changed."
        settings = Settings()
    controller = Controller(settings)
    controller.pause(args.paused or bool(warning))
    if args.headless:
        print("League Companion is running. Ctrl+C stops it.")
        controller.start()
        try:
            while True:
                try:
                    kind, payload = controller.events.get(timeout=1)
                    if kind in ("status", "phase", "log"):
                        print(f"[{kind}] {payload}", flush=True)
                except Empty:
                    continue
        except KeyboardInterrupt:
            controller.stop()
        return 0
    try:
        from .ui import run_ui
    except ImportError:
        print("Tkinter is unavailable. Install a Python build with Tk, or use --headless.", file=sys.stderr)
        return 2
    if warning:
        controller.events.put(("log", warning))
    run_ui(settings, args.config, controller)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
