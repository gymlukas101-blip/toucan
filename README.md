# TASKRUNNER

## Setup

Run `.\bootstrap.ps1` once after cloning. It sets `core.hooksPath` to
`.githooks` in the clone’s local Git configuration and runs the secret and
memory checks. Git tracks the hook script. Each clone needs its own hook
path setting.

## Global rules backup

[AGENTS.global.md](AGENTS.global.md) is a versioned backup of the live global
rules at `C:\Users\gymlu\.codex\AGENTS.md`. Refresh the backup after editing
the live file. [AGENTS.md](AGENTS.md) contains the project rules.
