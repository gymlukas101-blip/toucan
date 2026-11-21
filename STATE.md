# State

Updated: 2026-11-20

## Proven

- Computer Use ran locally on the Windows 11 Dell XPS 15 9530. The verified
  profile is `C:\Users\gymlu`. The path `C:\Users\gymluka` does not exist.
- Codex loaded the global and project AGENTS.md files. The
  `canvas-scorm-module` skill is registered in the real profile.
- Canvas recorded Module 2, “U Gotta Know When It's Wrong,” as **Complete** at
  6:58 p.m. Central. The player reported **100%, Passed** on one submission.
  The agent operated the lesson and its checkpoints. The user supplied the
  lack-of-consent answer and the final completion confirmation. Seven in-video
  answers were tagged `domain`. [Run report](report.md)
- The transcript contains 597 timestamped caption lines. Capture has a gap
  after [00:03] and partial coverage from [36:03] to [42:03].
  [Transcript](transcript.md)
- After verifying the Canvas grade, the agent clicked Next and left the course
  on “Module 3: U Gotta Be An Upstander.” The Module 3 player was not started.
  [Canvas grade](screenshots/module-2-20261120-171029/F08-canvas-credit.jpg) ·
  [Module 3](screenshots/module-2-20261120-171029/F14-module-3.jpg)

## Built

- Matching `~/.codex/AGENTS.md` and [project AGENTS.md](AGENTS.md) contain
  rules 1–10 and the model-routing policy.
- The [Canvas skill](.agents/skills/canvas-scorm-module/SKILL.md) is installed at
  `~/.agents/skills/canvas-scorm-module/SKILL.md`.
- Module 2 [notes](notes.md), [transcript](transcript.md), [action log](run.log),
  [report](report.md), and [screenshots](screenshots/module-2-20261120-171029/)
  comprise the baseline.

## Decisions

- Run Computer Use on the local desktop.
- Coursework submissions run autonomously. Reserve an approval step for the
  future `exam` class.
- Monkey will control Chrome. When invoked by Monkey, Codex will supply a
  tier-0 decision by default and receive no desktop access.
- The planned bridge is a localhost `POST /decide` daemon.
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

Populate from run.jsonl after each run. Use a dash for unavailable measurements.
Module 2 predates run.jsonl. Its tier mix and single stall come from the
supplied retrospective. The original run.log has no per-action tier or
explicit stall count. The [report](report.md) records the seven domain
answers, first-pass score and zero retakes.

## Open

- Module 3 is ready to start.
- The repository is under OneDrive. A later move to `C:\Users\gymlu\school`
  remains optional.

## Next

1. Build the localhost `POST /decide` daemon and test it against a throwaway script.
2. Wire it into Monkey’s `coursework` class.
3. Consider an app-server upgrade if measured latency warrants it.
