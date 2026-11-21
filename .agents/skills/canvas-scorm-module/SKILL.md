---
name: canvas-scorm-module
description: Complete a Canvas assignment that launches an external SCORM or
  interactive-video module in a new window (Articulate Rise, U Got This!,
  AlcoholEdu, Decisions). Use when a Canvas assignment shows "Load ... in a
  new window" and a 20-60 minute video. Not for in-Canvas quizzes or
  file-upload assignments.
---

# Canvas SCORM module

## Preconditions
- Canvas assignment page open and signed in
- Chrome focused, one window, no other tabs
- powercfg timeouts disabled

## Steps
1. Screenshot the assignment page. Log title, URL, points, and instructions
   verbatim to notes.md.
2. Click the launch button. Confirm a new window opened on the module, not a
   Canvas error page.
3. Fullscreen it (F11). Confirm the Start Course overlay.
4. Turn captions on if a CC control exists and captions are off.
5. Press play. Confirm the timer is moving before leaving the player.
6. Run the poll loop.
7. On completion: quiz, score read-back, return to Canvas, verify badge,
   click Next.
8. Write report.md. Restore power settings.

## Poll loop
Start caption capture as each slide opens. Read from the built-in transcript
or captions panel when available. Append each new line verbatim to
transcript.md as `[MM:SS] text`, independently of the screenshot interval.
Take a settled screenshot every 45 seconds while the media playhead advances.
For ordinary waits, check every 30 seconds. Compare each frame with the previous
one and reassess when it changes.
- Prompt or checkpoint present -> screenshot, state in one line what it asks,
  choose and tag the answer under AGENTS.md rule 7, verify it registered, resume
- Frame unchanged for 30 seconds -> check for a pause or stall, resume playback
  if stalled, and log the result. Stop after three consecutive stalls.
- Never screenshot into a transition; wait for the frame to settle.

## Never
- Reload, close, or back out of the module window
- Batch transcript writes
