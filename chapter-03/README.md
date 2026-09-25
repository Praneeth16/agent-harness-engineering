# Chapter 3: Build the smallest useful runtime

Open [chapter03.ipynb](chapter03.ipynb) and run all cells in order from a fresh Python kernel. Sections 1 to 10 use the standard library only (Python 3.10 or newer). Section 11 optionally replaces the stand-in planner with a live model and skips itself when none is configured.

The notebook loads the Chapter 2 contracts, adds patient P041 (nine clinical notes, two mentioning anticoagulants), and builds the runtime from the chapter: `Run` and `Budget` with an append-only event log (Listing 3.1); three tools that write into the run, `search_notes`, `read_note`, and `evaluate_rule` (Listing 3.2); the allowlist and gate (3.3); one step (3.4); the loop with completed, blocked, failed, and cancelled endings (3.5); the stand-in planner (3.6); and the misbehaving models of Table 3.2 (3.7). Then the three exercises with solutions, then P041 through a live planner.

Chapter 4 changed one line of `drive`: the step budget is checked directly (`run.count("proposal") >= run.budget.steps`) rather than by assembling a context, so that Chapter 4's `context` event is written once per step. The notebook and the book's Listing 3.5 both carry the change.

## Expected results

- The stand-in planner completes P041 in 7 proposals and 2 reads: age met, marker met, anticoagulant exclusion not met citing note n25; note n26 recorded as a problem (no date) without changing the result. The full event log prints, 15 events ending in a `stop`.
- Table 3.2 reproduces: `loopy` and `eager` are blocked by the 12-step budget; `rogue` is refused once (`tool not allowed: write_record`) and then completes; a reader that raises ends the run `failed` with `RuntimeError` in the reason; a read budget of one ends `blocked: read budget spent`; a cancelled run ends `cancelled`.
- Exercise 3.1 adds a "planner cannot make progress" stop after two refused finishes in a row (`eager` now stops in 2 steps). Exercise 3.2 adds a `draft_packet` tool and extends the completion check. Exercise 3.3 is answered against the live run.
- No cell ends with an unhandled exception.

## Optional: a live planner

Section 11 defines `live_planner`, which receives exactly the context dictionary the stand-in receives plus the tool list, and must return one JSON proposal. The runtime does not change. The event log of the live run prints with endpoint and date; a wrong argument name or an unknown tool shows up as a gate event, and the run still ends in one of the five states. Set-up is in the Chapter 1 notebook, Section 8.

## Run without opening Jupyter

`chapter03.py`, attached inside the book PDF, is the Chapter 2 definitions plus Listings 3.1 to 3.7, P041, and the trial loop as one script: `python3 chapter03.py` prints Table 3.2. Or execute the notebook headlessly as described in the Chapter 1 README, substituting `chapter-03/chapter03.ipynb`.

The checked-in notebook has cleared outputs. Assertions are development checks; do not run with `python -O`. Nothing here touches a patient system or writes outside the process.
