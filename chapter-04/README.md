# Chapter 4: Engineer the context layer

Open [chapter04.ipynb](chapter04.ipynb) and run all cells in order from a fresh Python kernel. Sections 1 to 9 use the standard library only (Python 3.10 or newer). Section 10 optionally runs a live planner on the assembled bundle and skips itself when none is configured.

The notebook loads Chapters 2 and 3, adds patient P042 (thirty notes over a year, two mentioning anticoagulants), and builds the context assembler from the chapter: the six-field `Context` bundle and the `pending_for` block derived from the whole log (Listing 4.1); `assemble`, which shrinks the recent window oldest-first until the bundle fits a size limit and writes a `context` event before the planner sees it (Listing 4.2); `inspect_log`, a tool into the archive, and `planner_v2`, which works from the pending block (Listing 4.3); and `naive_context`, the invariants, and the exit test (Listing 4.4). Then the three exercises with solutions, a sketch of a bounded recursive scan, and the live run.

One line of Chapter 3 changed to support this chapter: `drive` now checks the step budget directly rather than assembling a context to read `steps_left`, so the `context` event is written exactly once per step. The Chapter 3 notebook and the book's Listing 3.5 carry the change.

## Expected results

- P042 completes under version 3 reading 2 of 30 notes; the naive bundle is 6,560 characters; the assembled bundle is 1,401 at a 2,400 limit, 1,141 at 1,200, and 834 at 700 with one observation left in the window. Four invariants hold at every size: rules carry only the selected version, the other version's range appears nowhere, unread hits match the log, rules without results match the log.
- One `context` event per step, each recording the bundle's size and how many observations were left out.
- Exercise 4.1: Chapter 3's `fake_planner` fails on the bundle (`KeyError: 'observations'`), recorded as a `failed` run; the defect is the planner's assumption about history. Exercise 4.2: a fifth invariant (no note text in the bundle) holds as built and catches a leaky `read_note`. Exercise 4.3: a "blood thinner" note is invisible to lexical search and the exclusion comes out `unknown`; the fixture that would justify vector search is written out, with the grounding check kept in place.
- No cell ends with an unhandled exception.

## Optional: a live planner on the bundle

Section 10's `live_planner_v2` receives the six-field bundle and the tool list including `inspect_log`. The log shows every context size, every archive lookup, and any gate refusal. Set-up is in the Chapter 1 notebook, Section 8.

## Run without opening Jupyter

`chapter04.py`, attached inside the book PDF, is Chapters 2 and 3 plus Listings 4.1 to 4.4 and P042 as one script; `python3 chapter04.py` prints the sizes and invariants. Or execute the notebook headlessly as described in the Chapter 1 README, substituting `chapter-04/chapter04.ipynb`.

The checked-in notebook has cleared outputs. Assertions are development checks; do not run with `python -O`. Nothing here touches a patient system.
