# Toucan lessons

Rules from Toucan runs. Read before every run. If a rule proves wrong, fix or
retire it in the same change and say why.

## Degradation vs stop

- A broken or missing control degrades a run; it does not stop one, as long as
  the item is still completable. *Why:* three caption-control misses stopped a
  runnable item at its first checkpoint with the answer on screen.
- Captions are an aid, not a gate. One attempt, then answer from the frame.
  *Why:* the transcript strategy had been treated as mandatory.
- Any stop must also hand off with code 3. A silent stop strands the caller.
  *Why:* the run halted without 3, so nothing took over.
- Locate a control from the frame before clicking. Video-area controls and
  player chrome are different surfaces. *Why:* CC lives in the video area.
- Convert screenshot pixels to page coordinates before a positional click.
  *Why:* Windows display scaling made the first CC click miss its target.

## Answering

- When the transcript is silent on an in-video checkpoint, use the question's
  framing and domain knowledge. Stop on a graded question only if it remains
  ambiguous after that attempt. *Why:* “Every sexual assault has one thing in
  common:” had one definitional answer. The previous rule paused the run until
  the user supplied it.
- Start caption capture on the first frame, before pressing play. Keep it
  active as each slide opens. *Why:* Module 2 lost opening narration. Seven
  in-video answers used domain knowledge, including the first checkpoint.
  [Caption coverage and answer record](report.md)

## Pacing

- Submit completion modules once. Reserve a retake for graded work. *Why:*
  Module 2 estimated 30–35 minutes, and Canvas awarded full completion credit
  on the first submission.

## Paths and identity

- Verify the Windows profile path before writing. *Why:* the GitHub account
  is gymlukas101-blip and the Windows profile is gymlu. Checking the proposed
  gymluka path caught the mismatch before the rules were written.
- Open the Canvas module-item link when only the item ID is known. Compare the
  resolved assignment heading and launch label with the requested module.
  The first signed-in read reached assignment 3108978 and showed Module 2.
- Diagnose screenshot timeouts and blank viewports separately from playback.
  An unused background tab timed out during the Module 2 dry run. A video
  stall requires an observation of the playhead in the player frame.
- A background Canvas launch button can fail Playwright's stability check
  while visible and enabled. A page-scoped forced click opened Module 3 after
  the ordinary click missed. The Storyline player used audio elements, and
  the video-only caption sampler recorded no captions in this run.
- Read the requirement label and the title link separately. Module 3 and
  Module 4 both showed “Score at least 100.0”. Only Module 3 had a link.
  Canvas also displays instruction headers as plain text.
