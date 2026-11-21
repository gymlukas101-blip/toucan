# Toucan lessons

Rules from Toucan runs. Read before every run. If a rule proves wrong, fix or
retire it in the same change and say why. Keep at most 12 active rules. Give
each new rule a new, increasing ID. On overflow, delete the rule with the
fewest recorded uses across runs. Break ties by deleting the lowest ID first.

## Degradation vs stop

- [L08] A broken or missing control degrades a run; it does not stop one, as long as
  the item is still completable. *Why:* three caption-control misses stopped a
  runnable item at its first checkpoint with the answer on screen.
- [L09] Captions are an aid, not a gate. One attempt, then answer from the frame.
  *Why:* the transcript strategy had been treated as mandatory.
- [L10] If work stops while the item is still active, hand off with code 3.
  A silent stop strands the caller.
  *Why:* the run halted without 3, so nothing took over.
- [L11] Locate a control from the frame before clicking. Video-area controls and
  player chrome are different surfaces. *Why:* CC lives in the video area.
- [L13] Convert screenshot pixels to page coordinates before a positional click.
  *Why:* Windows display scaling made the first CC click miss its target.
- [L14] A completion redirect or detached player frame ends the item. Stop
  caption capture, leave the player alone, and check its row on the Canvas
  Modules page. Only a Canvas completion indicator confirms credit.
  *Why:* Module 3 redirected to “Thank you” while Canvas still showed
  “Score at least 100.0” and kept Module 4 locked.

## Answering

- [L03] Start caption capture on the first frame, before pressing play. Keep it
  active as each slide opens. *Why:* Module 2 lost opening narration. Seven
  in-video answers used domain knowledge, including the first checkpoint.
  [Caption coverage and answer record](report.md)

## Pacing

- [L04] Submit completion modules once. Reserve a retake for graded work. *Why:*
  Module 2 estimated 30–35 minutes, and Canvas awarded full completion credit
  on the first submission.

## Paths and identity

- [L05] Open the Canvas module-item link when only the item ID is known. Compare the
  resolved assignment heading and launch label with the requested module.
  The first signed-in read reached assignment 3108978 and showed Module 2.
- [L06] Diagnose screenshot timeouts and blank viewports separately from playback.
  An unused background tab timed out during the Module 2 dry run. A video
  stall requires an observation of the playhead in the player frame.
- [L12] A background Canvas launch button can fail Playwright's stability check
  while visible and enabled. A page-scoped forced click opened Module 3 after
  the ordinary click missed. The Storyline player used audio elements, and
  the video-only caption sampler recorded no captions in this run.
- [L07] Read the requirement label and the title link separately. Module 3 and
  Module 4 both showed “Score at least 100.0”. Only Module 3 had a link.
  Canvas also displays instruction headers as plain text.
