# Toucan lessons

Rules from Toucan runs. Read before every run. If a rule proves wrong, fix or
retire it in the same change and say why. Keep at most 15 active rules. Give
each new rule a new, increasing ID. On overflow, delete the rule with the
fewest recorded uses across runs. If a tie spans the cutoff, compare the run
evidence and retain rules that cover recurring failures.

## Degradation vs stop

- [L08] A broken or missing control degrades a run; it does not stop one, as long as
  the item is still completable. *Why:* three caption-control misses stopped a
  runnable item at its first checkpoint with the answer on screen.
- [L09] Captions are an aid, not a gate. One attempt, then answer from the frame.
  *Why:* the transcript strategy had been treated as mandatory.
- [L10] If work stops while the item is still active, hand off with code 3.
  A silent stop strands the caller.
  *Why:* the run halted without 3, so nothing took over.
  If a Rise menu keeps opening and closing on the same lesson, use the
  numbered next-lesson link in the open player. This took ActWIse from its
  introduction through all nine lessons in the same attempt.
  DatingWIse displayed 13% after its introduction. Its next-lesson panel held
  one link to the next lesson in the same player document. Check that panel
  when the decision menu offers only controls for a finished activity.
- [L11] Locate a control from the frame before clicking. Video-area controls and
  player chrome are different surfaces. On a question slide, select an answer
  and verify its feedback before using Next. *Why:* CC lives in the video area.
  In Module 3, Next repeated a response-required alert. Choosing a displayed
  answer advanced the same reflection on the rerun.
  Canvas exposed “Take the Survey” as a button. Verify that question fields
  open after activating it, and verify submission on the Modules page. The
  Post-Survey kept its saved answers when its confirmation dialog closed.
  Bind each survey choice to its question before checking for an answer.
  For “select all that apply” and “choose all”, check each plausible option
  and confirm the whole selection before continuing.
  DatingWIse used the label “Group of answer choices” for ten different
  questions. Verify each group separately before offering Submit.
  Inspect the link text on native quiz entry pages. Decisions Part 2 showed
  “Take the Quiz” while the player menu reported zero controls twice.
  Read the explicit role on that link and check for question fields after a
  click. Free Expression used `role="button"` and stayed on its entry page.
- [L13] Convert screenshot pixels to page coordinates before a positional click.
  *Why:* Windows display scaling made the first CC click miss its target.
  Canvas interactives and hotspot markers can ignore a synthetic click.
  When a named click leaves the frame and playhead unchanged, locate the
  same visible control again and retry once at its centre. Log the retry
  and verify the result. After three misses, wait and inspect the control again.
- [L14] Bind the course after its launch redirects settle. An embedded video
  can give way to a quiz inside the same course. Follow that transition and
  record the quiz, Submit, and the score on the active results card. Rise
  prefilled an inactive card with 0% before Module 3's quiz began.
  A launch can open a vendor on another origin. Record that origin and the
  player’s iframe origins on this item. Continue on its recorded origins.
  When the course ends, stop capture and verify its Canvas row. Stop input
  if the window moves outside Canvas and the origins recorded for this item.
  Close a dead player before the next item once its item is terminal and
  verified on Canvas. Preserve the current player and all running items.
  *Why:* Module 3's “Thank you!” page left Canvas pending. Its fresh launch
  also changed documents on the way to a live lesson.

## Answering

- [L02] When the transcript is silent on an in-video checkpoint, use the question's
  framing and domain knowledge. Stop on a graded question only if it remains
  ambiguous after that attempt. *Why:* “Every sexual assault has one thing in
  common:” had one definitional answer. The previous rule paused the run until
  the user supplied it.
- [L03] Start caption capture on the first frame, before pressing play. Keep it
  active as each slide opens. *Why:* Module 2 lost opening narration. Seven
  in-video answers used domain knowledge, including the first checkpoint.
  [Caption coverage and answer record](report.md)

## Pacing

- [L15] Click a sole visible, menu-approved non-answer button at T0 when the
  frame has changed since the last click. Leave answer choices to the decision
  step. Bound that step to 45 seconds and re-read the page on timeout.
  *Why:* a 70.891-second decision preceded Module 3's expired session.
  Decision latency is the likely cause. The session log did not record a cause.
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
  After reloading an ordinary hidden tab, capture its viewport offscreen.
  The background test page had restored its text while its window capture timed out.
- [L12] A background Canvas launch button can fail Playwright's stability check
  while visible and enabled. A page-scoped forced click opened Module 3 after
  the ordinary click missed. The Storyline player used audio elements, and
  the video-only caption sampler recorded no captions in this run.
  If nested frames share a name, locate the visible launch button in its
  own frame. StandUp had two frames named `ScormContent`. Only the inner
  frame displayed “Launch Course”, and both name-based clicks missed.
  StandUp also opened a player while its Canvas assignment was settling.
  Match the existing window's opener to the assignment and check its title
  and START control before issuing another launch.
- [L07] Read the requirement label and the title link separately. Module 3 and
  Module 4 both showed “Score at least 100.0”. Only Module 3 had a link.
  Keep a requested locked item in the plan by its row ID until its link opens.
  The Module 4 preview listed the locked Post-Survey second with the label
  “Submit”. Canvas also displays instruction headers as plain text.
  Preserve an explicitly requested sequence during rescans. Alcohol Awareness
  listed Part 2 first, while the owner requested Decisions followed by Part 2.

## Attestations

- [L16] Attestations are not escalations. The human is asleep; answer yes and submit.
  *Why:* Module 4 stopped at the policy acknowledgement for a human who had
  already reviewed it.
  Answer personal questions using the owner’s profile and standing choices.
  Record each new answer immediately and reuse it on later questions.
  *Why:* rule 10 triggered handoffs on questions with no objective answer.
