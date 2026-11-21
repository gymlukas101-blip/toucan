# Toucan

These schoolwork rules apply to every session under the Toucan project root.
The current checkout is `C:\Users\gymlu\OneDrive\Documents\Toucan`.

## Environment
- Windows 11, single display. The owner may keep working during browser runs.
- Before any run: `powercfg /change monitor-timeout-ac 0` and
  `powercfg /change standby-timeout-ac 0`. After: restore to 15 and 30.

## Browser

- Use the Playwright CDP driver in `tools/browser/` for every HTTP(S) target.
  T0 and T1 browser actions both use CDP. Send input to the task's tab and
  read the target frame, including cross-origin frames.
- Connect to Toucan's Chrome profile at `127.0.0.1:9222`. The owner's main
  Chrome profile stays independent. Keep the Toucan window open behind other
  windows. Never minimize it.
- Computer Use requires the foreground. After two CDP stalls, queue exit code
  3 hands off to Computer Use with the player open and the item still running.
  Continue in the same player and append captions to the same transcript.
  Log the foreground warning and driver change. The owner handles NetID and Duo
  in the Toucan window.
- Before a player handoff, record its current URL through the driver with
  the query and fragment removed. Keep surveys on the browser driver, answer
  each visible group and verify submission on Canvas. Use Agree, Satisfied or
  4 out of 5 on positive rating scales.
- A blank capture or a Chrome discard signal triggers one recovery reload on
  an ordinary page. Preserve loaded course players under rule 3. Report a
  missing capture separately from a playback stall. Check playback through
  the media playhead in its frame.

## Layout
- notes.md       rolling notes, appended as you go
- transcript.md  verbatim caption text, `[MM:SS] text`, appended as it plays
- run.jsonl      one JSON object per action (see below)
- report.md      final write-up

## Run log format

run.jsonl, one object per line, append-only:

```text
{"t":"<ISO8601>","action":"<verb>","target":"<what>","result":"<ok|miss|stall|timeout|ambiguous|aborted|domain|escalate|handoff|item-ended|blocked>","tier":"<T0|T1|T2>","basis":"<MM:SS|domain>","ms":<int>,"driver":"<cdp|computer>"}
```

result carries the event word the digest reads. Use "domain" on any
domain-sourced answer. Record each action's elapsed milliseconds in ms.
For a retake, use the action "retake" and record its result.
Record `cdp` for browser actions and `computer` for native handoffs. Older logs may omit `driver`.
When an active lesson drives a decision, record its ID once on that decision
as `"rule_used":"L14"` (using the applicable ID). Omit the field otherwise.
Use `item-ended` for the end of a player session and check the Canvas Modules
row before marking the item done.
For scored modules, log the completion quiz, Submit click, and posted score as
separate observations. If an exit screen appears first, record the last
observed slide and verify the exact item on Canvas.

Example record

```json
{"t":"2000-01-01T00:00:00Z","action":"answer","target":"example checkpoint","result":"domain","tier":"T1","basis":"domain","ms":250,"driver":"cdp"}
```

## Rules
1. Prefer structured control — extension, DOM, MCP — over clicking pixels.
   Use @Computer only where there is no programmatic path.
2. Screenshot before every action; read the result after. Never act on a
   remembered layout. Never screenshot more often than once every 10 seconds.
3. Once a SCORM module loads, never close, reload, or navigate away from it.
   The launch URL is signed and progress is server-tracked.
4. Never click "EXIT COURSE", browser Back, or any start-over control.
5. Append to files continuously. Never batch writes to the end of a run. Log
   every action to run.jsonl in the format above.
6. Never type a password. A NetID or Duo prompt means stop and hand off.
7. Never block waiting for a human. Decide and keep moving. Answer in-video
   checkpoints and graded questions alike from transcript.md when it supports
   you, otherwise from the framing and standard domain knowledge. Tag every
   answer with its basis: a transcript timestamp, or the literal "domain".
8. Retake only when the score matters — graded quizzes and tests. On a
   completion module the first pass is final: submit and move on. Never a
   second retake either way. Record the score and every "domain"-tagged answer
   in report.md.
9. Confirmation and attestation questions are answered affirmatively without
   waiting for the human: "have you completed", "I have reviewed/read X",
   "I understand". Select the affirmative option, submit, continue.
10. Stop only on a hard block: a password or Duo prompt, a Canvas error page,
    three consecutive stalls, or a question still ambiguous after a domain
    attempt. Log it, write the report, end the task. Never wait.

## Report
report.md always contains: assignment, start and end time, completion status,
quiz score, each answer with the transcript line it came from, and anything
unresolved.

## Model routing

Default to the cheapest tier that can succeed. Escalate only on evidence.

- T0 mechanical: play, pause, continue, next, submit, confirm, scroll, or
  clicking an already-identified target. GPT-6 Luna, Low, Fast. Use CDP on browser targets.
- T1 visual or judgment: read a caption, match an option to transcript.md,
  locate an unnamed control, recover from one miss. GPT-6.1 Terra, Medium, Fast.
  Read the viewport and frames through CDP.
- T2 hard or high-stakes: ambiguous question, conflicting options, a graded item
  worth real points, recovery after two failures. Astra or Sol, high.

Escalate T0 -> T1 -> T2 on: two misses on one control, an unpredicted dialog, a
caption gap the next question depends on, or a stall. De-escalate after one
clean step. Never sit in T2 by default.

Poll every 45 seconds while the media playhead advances. Keep the 30-second
interval for ordinary waits and recheck after an action. A sole visible
non-answer button in the allowed menu can run at T0 under L15. Recheck the
screen after a 45-second decision timeout and log a stall if no such button is
available. Only re-reason when the frame changes.

Classify before acting. Reply with JSON only for the routing decision:

```json
{"tier":"T0|T1|T2","model":"...","effort":"...","why":"<8 words>","escalate_if":"<what would prove this wrong>"}
```

Use T0 for a purely mechanical action with an identified target. Use T1 to
read text, match an option to transcript.md, or find an unnamed control. Use
T2 for an ambiguous question, a graded item worth real points, or recovery
after two failures. Note what would escalate the decision, then act.

## Memory recall

- On the first turn of an assignment, load memory/courses/<course>.md if it
  exists. Cap the injected text at 2000 characters.
- Never inject memory/inferences.md unless asked.
- Append new durable facts to the course file as you learn them, using the
  format in memory/README.md. src is transcript, screenshot, or domain.
- Record control locations and quiz shape, not individual answers. Answers
  belong in report.md.
- If a new fact contradicts an existing one, append a "may replace" hint; do
  not refuse.

Keep whole fact lines within the recall limit. Put a "may replace" hint in
the fact text before its metadata. When a correction is confirmed, move the
old line to memory/superseded.md and exclude it from future recall.

Read LESSONS.md before each run. After every run, run tools/collect_lessons.py
on that run's run.jsonl, compare candidates with report.md, and append or fix
a rule in LESSONS.md. Keep at most 15 active rules. After editing, run
`py -3 tools/collect_lessons.py <run.jsonl> --prune` to delete excess rules
by recorded use across project and local Toucan run logs. Pass
`--history <directory>` for runs stored elsewhere. Older logs without rule IDs
contribute no usage evidence. Resolve ties at the cutoff from run evidence.
Keep IDs stable across edits and retirements.
Update the Metrics table in STATE.md from the run's action log.
For a multi-course run, finish the current course report and compare it with
the output of tools/collect_lessons.py before starting the next queue. Apply
the resulting lesson edit and record its ID on decisions it drives. Keep one
observed control or navigation fact per vendor host in that course's memory.
Keep the owner's personal response history in the local memory/profile-answers.md.
