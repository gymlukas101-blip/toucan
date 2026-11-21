# Toucan

Toucan is a local Windows agent for Canvas coursework. It records transcripts,
quiz results, and lessons from each run.

## Setup

Run `.\bootstrap.ps1` once after cloning. It sets `core.hooksPath` to
`.githooks` in the clone’s local Git configuration and runs the secret and
memory checks. Git tracks the hook script. Each clone needs its own hook
path setting.

## Parallel browser use

Toucan sends browser input directly to a tab in its own Chrome profile.
You can type or use the pointer in another app while it works. Leave the
Toucan window open behind your other windows. Minimizing it can throttle
rendering.

From the checkout, create the runner environment and start the profile once.
Install Google Chrome and make the signed-in Codex CLI available on your
PATH for tasks that request model decisions.

```powershell
py -3 -m venv "$env:LOCALAPPDATA\Toucan\venv"
$toucanPython = "$env:LOCALAPPDATA\Toucan\venv\Scripts\python.exe"
& $toucanPython -m pip install -r tools/browser/requirements.txt
& $toucanPython -m tools.browser.launch
```

Open Canvas in that window and complete NetID and Duo yourself. Chrome
keeps that session in `%LOCALAPPDATA%\Toucan\chrome-profile`. The launcher
verifies the profile that owns local port 9222 before attaching.
[One-time sign-in](tools/browser/LOGIN.md)

To read an assignment, give `--preview` its resolved URL. To run a task,
give `--task` a JSON file with `url`, `goal`, `done_text` and an `actions`
list of permitted controls. For example, a click entry is
`{"action":"click","target":"Continue"}`. Use a separate run directory for each task.

```powershell
$assignmentUrl = Read-Host 'Resolved Canvas assignment URL'
$runRoot = Join-Path $env:LOCALAPPDATA 'Toucan\runs'
& $toucanPython -m tools.browser.run --preview $assignmentUrl --run-dir "$runRoot\preview-001"
& $toucanPython -m tools.browser.run --task .\task.json --run-dir "$runRoot\task-001"
```

The action log identifies the driver. A blank viewport or Chrome's discard
signal permits one reload on an ordinary page. A screenshot timeout retries
the capture in place. Signed launches and course players stay in place.
A native fallback must name a non-browser app. Its handoff warns that
Computer Use needs the foreground.

## Global rules backup

[AGENTS.global.md](AGENTS.global.md) is a versioned backup of the live global rules.
After editing `~/.codex/AGENTS.md`, re-copy it to `AGENTS.global.md`.
[AGENTS.md](AGENTS.md) contains the project rules.
