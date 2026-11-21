# Toucan state

Updated: 2026-11-21

## Proven

- Canvas recorded Module 3 as Complete on November 21 at 2:02 a.m. Central.
  The quiz showed 100% and Passed after one submission. The Modules row read
  “Scored at least 100.0”. The evidence is in the local Toucan run
  `module3-rerun-20261121T071810Z`.
- Computer Use ran locally on the Windows 11 Dell XPS 15 9530. The verified
  profile is `C:\Users\gymlu`. The path `C:\Users\gymluka` does not exist.
- Codex loaded the global and project AGENTS.md files. The
  `canvas-scorm-module` skill is registered in the real profile.
- Canvas recorded Module 2, “U Gotta Know When It's Wrong,” as **Complete** at
  6:58 p.m. Central. The player reported **100%, Passed** on one submission.
  Toucan operated the lesson and its checkpoints. The user supplied the
  lack-of-consent answer and the final completion confirmation. Seven in-video
  answers were tagged `domain`. [Run report](report.md)
- The transcript contains 597 timestamped caption lines. Capture has a gap
  after [00:03] and partial coverage from [36:03] to [42:03].
  [Transcript](transcript.md)
- After verifying the Canvas grade, the agent clicked Next and left the course
  on “Module 3: U Gotta Be An Upstander.” The Module 3 player was not started.
  [Canvas grade](screenshots/module-2-20261120-171029/F08-canvas-credit.jpg) ·
  [Module 3](screenshots/module-2-20261120-171029/F14-module-3.jpg)
- In Toucan's signed-in Chrome profile, Canvas module item `9791076` resolved
  to assignment `3108979`. The heading was “Module 3: U Gotta Be An Upstander”
  and the button read “Load Module 3: U Gotta Be An Upstander in a new window”.
  The identity check issued zero clicks. Assignment `3108978` belongs to Module 2.
- The Windows proofs covered background clicks, concurrent Notepad input,
  cross-origin frames, a closed CDP port and recovery after a real window blur.
  In the 20-second occupancy proof, Chrome received 10/10 clicks while Notepad
  received 42/42 characters. [Parallel-use tests](tests/test_parallel.py)
- A hidden fixture tab recovered after one reload of a blank viewport. A
  black video kept advancing during a failed capture, and the embedded course
  fixture retained its document. The Chrome discard flag was simulated on
  a disposable tab. All 12 tests passed.

## Built

- The [project rules](AGENTS.md) specify the CDP browser driver, schoolwork
  rules 1–10 and model routing. The live global rules have a separate
  [versioned backup](AGENTS.global.md).
- The [Canvas skill](.agents/skills/canvas-scorm-module/SKILL.md) is installed at
  `~/.agents/skills/canvas-scorm-module/SKILL.md`.
- Module 2 [notes](notes.md), [transcript](transcript.md), [action log](run.log),
  [report](report.md), and [screenshots](screenshots/module-2-20261120-171029/)
  comprise the baseline.

## Decisions

- Renamed TaskRunner to Toucan 2026-11-20. Repo, folder, and agent name. Files in report.md, transcript.md and screenshots/ keep the old name — they are historical.
- Use Playwright over CDP for HTTP(S) tasks. The runner verifies the Chrome
  process that owns the loopback port and its dedicated profile. The owner's
  main Chrome is untouched. Native Computer Use takes the foreground only
  for a named non-browser target.
- Coursework submissions run autonomously. Reserve an approval step for the
  future `exam` class.
- The [runner](tools/browser/run.py) uses `codex exec --output-schema` for
  decisions and sends controls to one tab. It records which driver acted.
- A completion module ends after the first submission. For a graded quiz or
  test below full marks, use at most one offered retake.

## Measured

- The run lasted **1h 50m 35s**, from 5:10:29 p.m. to 7:01:04 p.m. Central.
  The player estimated 30–35 minutes. The goal tracker counted **7m 48s** of
  active time.
- The log records pauses for user input. It does not measure model or effort
  by action, so the time attributable to Astra Max is unknown.
- The next run will use Luna Low for mechanical steps, Terra Medium for visual
  judgments, Fast mode at those tiers, and a 30-second polling interval.

## Metrics

| date | module | wall | active | turns | T0/T1/T2 | domain | stalls | retakes | score |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-20 | 2 | ~1h50m | 7m48s | - | mostly T2 | 7 | 1 | 0 | 100% |
| 2026-11-20 | 3 assignment identity read | ~4s | - | - | 3/1/0 | 0 | 0 | 0 | - |
| 2026-11-20 | 3–4 DOM queue preview | 4.65s | - | - | 4/2/0 | 0 | 0 | 0 | - |
| 2026-11-21 | 3 incomplete run | ~7m | - | - | 62/44/0 | 0 | 2 | 0 | - |
| 2026-11-21 | 3 continuation | ~17m | - | - | 84/108/0 | 1 | 0 | 0 | - |
| 2026-11-21 | 3–4 Canvas completion check | 6.58s | - | - | 3/3/0 | 0 | 0 | 0 | - |
| 2026-11-21 | 3 rerun and grade verification | 43m45s | - | 87 | 217/284/0 | 3 | 1 | 0 | 100% |

Populate from run.jsonl after each run. Use a dash for unavailable measurements.
The Module 3 rerun counts the 87 decisions and 501 logged actions through
the Canvas grade read. An additional screenshot captured the settled score dial.
Module 2 predates run.jsonl. Its tier mix and single stall come from the
supplied retrospective. The original run.log has no per-action tier or
explicit stall count. The [report](report.md) records the seven domain
answers, first-pass score and zero retakes.

## Open

- In course 532511, Module 4 unlocked after Module 3 posted. Its row shows
  “Score at least 100.0”. The Post-Survey remains locked. The Module 3 rerun
  stopped after verifying its own grade.
- Toucan lives at `C:\Users\gymlu\OneDrive\Documents\Toucan`. A later move
  to `C:\Users\gymlu\school` remains optional.

## Next

For a fresh coursework chat, move the chat “Inspect open desktop windows” to
Toucan, remove the old TaskBot entry, then add or select the project at
`C:\Users\gymlu\OneDrive\Documents\Toucan`. Start a fresh chat in that
project and verify it answers `Quote rule 1 from AGENTS.md.`
