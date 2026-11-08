# Quickstart: Desk

Goal: a stranger on a clean machine reaches a running Desk (a ticket store, the `drew` command line and the web UI) in under fifteen minutes, or stops at one actionable message.

## Prerequisites (with minimum versions)
- Windows 11, macOS or Linux (developed and tested on Windows 11)
- Python 3.11 or newer (3.13 is what the author uses) and [uv](https://docs.astral.sh/uv/) 0.5 or newer
- git, for the optional sync and for `drew doctor` / `drew upgrade`
- No accounts, keys or network services. The UI loads its web fonts from Google Fonts when online and falls back to system fonts offline.

## Install
```powershell
# Windows (PowerShell)
git clone https://github.com/jameseby21/Desk.git; cd Desk
uv tool install .
drew init "$HOME\desk-store" --owner you
[Environment]::SetEnvironmentVariable("DESK_HOME", "$HOME\desk-store", "User")   # then open a new terminal
```
```bash
# macOS / Linux
git clone https://github.com/jameseby21/Desk.git && cd Desk
uv tool install .
drew init ~/desk-store --owner you && export DESK_HOME=~/desk-store
```
Type `drew`, not `desk`, on Windows: `desk.cpl` (Display Settings) shadows it.

## Run
```
drew-ui
```
Open http://127.0.0.1:8765. You should see the Today page with "Today's walk", the navigation (Today, Board, Projects, About, Settings) and, in the footer, `Desk <version> · <commit>`.

Make a first ticket (a project profile must exist first):
```
echo "# First" > "$DESK_HOME/projects/first.md"
drew new --project first --type chore "Try Desk"
drew list
```

## Refreshing Desk (stop, install, start)
After new Desk code is merged or checked out, one command does all three steps:
```
drew refresh                 # from the repo's working folder
drew refresh --ref main      # or from a branch, tag or commit of it
```
It stops the UI on port 8765 (by PID), installs with `uv tool install --force`, starts `drew-ui` and prints `drew --version`. On Windows the install runs in a separate process after the command exits (about 30 seconds); read `desk-refresh.log` in your temp folder, or run `drew doctor`. `Ctrl+C` in the `drew-ui` terminal also stops the server (within 2 seconds). Manual fallback, in any PowerShell window:
```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }   # or: drew stop
uv tool install --force "<path to the Desk repo>"
drew-ui
```

## Verify
```
uv run --no-sync pytest -q
drew validate
drew doctor
```
Expected: the tests pass, `validate` prints no errors and exits 0, and `doctor` lists the installed version and commit, the repo path, the tool and store paths and exits 0 when the installed copy matches the repo.

## If it fails
| You see | It means | Do |
| --- | --- | --- |
| Display Settings opens when you type `desk` | Windows runs `desk.cpl` first | type `drew` (or `desk.exe`) |
| `No desk.toml found` | Desk cannot find the store | pass `--home`, set `DESK_HOME`, or `drew init` in the store folder |
| The UI shows old behaviour after a code change | the installed tool is a copy from install day | stop the UI, `drew upgrade`, start `drew-ui` (see "Refreshing Desk" in the README) |
| `drew-ui` says `ModuleNotFoundError: No module named 'desk'` | an upgrade was interrupted and left the tool without its package | `uv tool install --force --reinstall <path to the Desk repo>` (`drew doctor` prints this command with your repo path) |
| On Windows `drew upgrade` says it handed the upgrade off | drew.exe cannot replace itself while it runs, so a separate process does it 2 seconds after the command exits | wait about 30 seconds, then `drew doctor`; the log is `desk-upgrade.log` in your temp folder |
| `drew upgrade` says it refuses and names a PID | the UI is still running | stop that PID (`taskkill /PID <pid> /T /F` on Windows, `kill <pid>` elsewhere) and run it again |
| `That form was already sent` | a button was pressed twice or a page was refreshed after posting | nothing was written twice; go back to the ticket |
| Port 8765 in use | another UI is running | `drew doctor` shows the PID; use `DESK_UI_PORT=8806 drew-ui` to run beside it |

## Next
- [Useful commands](COMMANDS.md)
- [Why Desk](WHY.md), the [README](../README.md)
