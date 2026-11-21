# School work

Applies to every session under the project root.

## Environment
- Windows 11, single display. Computer Use is foreground-only: the pointer and
  keyboard belong to the agent while a task runs. Assume the user is not at
  the machine.
- Browser: Chrome, driven through the browser extension. Firefox is the
  fallback if Chrome playback stalls.
- Before any run: `powercfg /change monitor-timeout-ac 0` and
  `powercfg /change standby-timeout-ac 0`. After: restore to 15 and 30.

## Layout
- notes.md       rolling notes, appended as you go
- transcript.md  verbatim caption text, `[MM:SS] text`, appended as it plays
- run.log        one line per action: time, action, observed result
- report.md      final write-up

## Rules
1. Prefer structured control — extension, DOM, MCP — over clicking pixels.
   Use @Computer only where there is no programmatic path.
2. Screenshot before every action; read the result after. Never act on a
   remembered layout. Never screenshot more often than once every 10 seconds.
3. Once a SCORM module loads, never close, reload, or navigate away from it.
   The launch URL is signed and progress is server-tracked.
4. Never click "EXIT COURSE", browser Back, or any start-over control.
5. Append to files continuously. Never batch writes to the end of a run.
6. Never type a password. A NetID or Duo prompt means stop and hand off.
7. Never block waiting for a human. Decide and keep moving. Answer in-video
   checkpoints and graded questions alike from transcript.md when it supports
   you, otherwise from the framing and standard domain knowledge. Tag every
   answer with its basis: a transcript timestamp, or the literal "domain".
8. Retake only when the score matters — graded quizzes and tests. On a
   completion module the first pass is final: submit and move on. Never a
   second retake either way. Record the score and every "domain"-tagged answer
   in report.md.
9. A confirmation question ("have you completed…") is answered yes when the
   work is visibly done. Never wait for the human on one.
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
  clicking an already-identified target. GPT-6 Luna, Low, Fast.
- T1 visual or judgment: read a caption, match an option to transcript.md,
  locate an unnamed control, recover from one miss. GPT-6.1 Terra, Medium, Fast.
- T2 hard or high-stakes: ambiguous question, conflicting options, a graded item
  worth real points, recovery after two failures. Astra or Sol, high.

Escalate T0 -> T1 -> T2 on: two misses on one control, an unpredicted dialog, a
caption gap the next question depends on, or a stall. De-escalate after one
clean step. Never sit in T2 by default.

Poll on a 30 second cadence. Only re-reason when the frame changed.

Classify before acting. Reply with JSON only for the routing decision:

```json
{"tier":"T0|T1|T2","model":"...","effort":"...","why":"<8 words>","escalate_if":"<what would prove this wrong>"}
```

Use T0 for a purely mechanical action with an identified target. Use T1 to
read text, match an option to transcript.md, or find an unnamed control. Use
T2 for an ambiguous question, a graded item worth real points, or recovery
after two failures. Note what would escalate the decision, then act.
