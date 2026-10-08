# League Companion

A small native desktop app for the League of Legends client. Python 3.11+, Tkinter, and the standard library. **No runtime pip dependencies, hosted service, Riot API key, or account password.**

## Run

1. Extract this folder somewhere permanent.
2. Install Python 3.11 or newer with Tcl/Tk. The standard Windows installer includes it.
3. Open League and sign in.
4. **Windows:** double-click `Start-Windows.bat`. **macOS:** from this folder, run `python3 -m league_companion` in Terminal.
5. Auto-accept is on by default, with a one-second delay. Other automations are off. Change options and choose **Apply & save** to retain them.

The app must run on the same computer as League. Keep it open while queueing. The close button exits; it does not minimize to a hidden tray. **Pause automation** stops subsequent requests; a request already sent to the client cannot be undone.

Alternative launch commands from this folder:

```sh
python -m league_companion --paused
python -m league_companion --headless
python -m league_companion --config settings.local.json
```

Use `python3` instead of `python` where appropriate. Headless mode reads the same saved preferences; Ctrl+C stops it. To resume after launching with `--paused`, use the graphical interface. An invalid settings file starts the GUI paused with defaults and leaves the original file intact. Headless mode exits on invalid settings.

## Features

| Option | Behavior |
| --- | --- |
| Accept matches | Accepts an unanswered, active ready check after the configured delay. |
| Pick priorities | Chooses the first available champion in your ordered list during your active pick turn. |
| Lock in | Separately enabled. Completes a pick made by this app. |
| Ban priorities | Bans an available priority champion during your active ban turn; avoids teammates' picks and hovers. |
| Summoner spells | Applies your two chosen spells once per champion-select session. |
| Rune page | Selects an existing saved page; does not create or overwrite pages. |
| Position preferences | Sets your primary/secondary roles in lobbies that expose position selection. |
| Reconnect | Uses the client's reconnect action when the gameflow enters Reconnect. |
| Return to lobby | Uses Play Again after the end-of-game phase. |
| Queue timeout | Cancels a search after the configured number of seconds. Zero disables it. |

The champion picker loads names and IDs from your local client. Add champions in priority order. Existing manual pick/ban selections are preserved. If every configured choice is unavailable, the app leaves the decision to you; it does not choose an arbitrary fallback or dodge. Modes with different selection flows may not support draft features. The client validates spell availability, rune pages, and all other actions.

Starting another queue remains a manual action. Return-to-lobby does not automatically start matchmaking. Auto-accept requires you to remain ready to play.

## Connection and troubleshooting

The app reads League's temporary `lockfile`. It checks standard Windows/macOS installation locations, then the running League client's executable location. You can set an exact path in **Connection** for custom installs or restricted process discovery:

- Windows example: `C:\Riot Games\League of Legends\lockfile`
- macOS example: `/Applications/League of Legends.app/Contents/LoL/lockfile`

Select the file while League is running. A configured path takes precedence over discovery. No need to copy the token or share the lockfile. Discovery runs periodically; restarting League and rotating its credentials are handled automatically. Multiple simultaneous League clients are not supported; select a specific lockfile if necessary.

If the status stays disconnected, sign into the full League client, verify the lockfile path, and confirm League and this app are running as the same OS user. Do not run it as administrator by default. An unavailable catalog can be retried with **Refresh**. Activity displays failed endpoints without response bodies or credentials. HTTP 404/405 can indicate an unsupported mode, client state, or changed endpoint; disable the affected option and check the current client API. Failed mutations use bounded retries and backoff.

Preferences are saved to:

- Windows: `%APPDATA%\LeagueCompanion\settings.json`
- macOS: `~/Library/Application Support/LeagueCompanion/settings.json`
- Other systems: `$XDG_CONFIG_HOME/LeagueCompanion/settings.json` (defaults to `~/.config`)

The code and headless tests can run on Linux, but this is not a way to run League on an unsupported platform.

## Architecture and development

```
league_companion/
  config.py       Typed preferences, validation, atomic JSON writes
  lcu.py          Lockfile discovery and loopback HTTPS transport
  engine.py       Phase-aware automation handlers and bounded retries
  controller.py   Background worker, reconnects, pause and settings cancellation
  ui.py           Tkinter settings, catalog picker, status and activity
  __main__.py     Desktop/headless entry point
tests/            Standard-library regression tests with simulated LCU responses
```

The UI owns the main thread. One worker polls gameflow, then reads only the resources needed by enabled automations. The polling interval defaults to one second; it is not a high-frequency loop. No background telemetry, public HTTP listener, or third-party network calls.

The HTTPS adapter always uses literal `127.0.0.1`, ignores environment proxy settings, and refuses redirects. It accepts the client's self-signed certificate only in its private transport context. Authentication exists in memory only, and credentials are excluded from object representations. Preferences are written atomically. The app has no in-game input automation.

To add an automation, add its setting and validation in `config.py`, its phase-scoped handler in `engine.py`, a control in `ui.py`, and a regression test using a fake client. Re-read current state before writes, preserve manual input, and keep retries bounded. No general-purpose plugin loader or arbitrary endpoint execution is needed.

Run tests from this folder:

```sh
python -m unittest discover -s tests -v
```

All 64 regression tests passed, and the headless runner was checked for startup and clean shutdown. The tests exercise simulated responses and local transport behavior. **A live League client and desktop display were not available in the build environment, so native UI rendering, real Windows/macOS client compatibility, and actual match acceptance remain unverified.** Try the core feature while present at your computer before enabling additional options.

## API scope and policy

These are technically available local client endpoints, **not an approved Riot integration**. Riot explicitly describes LCU as unsupported, with no guarantees of compatibility or change notices. API access does not itself mean an automation is permitted. Riot's developer policies require registration/review and restrict products that remove game decisions. This project has not been submitted or approved; check current requirements before using or distributing it. No account-safety guarantee is made.

References checked during development:

- [Riot: League Client API and game policies](https://developer.riotgames.com/docs/lol#league-client-api)
- [Riot: General developer policies](https://developer.riotgames.com/policies/general)
- [Client-generated LCU API reference, community hosted](https://lcu.kebs.dev/)

The API reference exposes far more capabilities than this focused tool. Sensitive account, purchase, loot, messaging, and gameplay actions are outside its current feature set. Endpoint availability varies by client version, queue, and account.

League Companion is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.
