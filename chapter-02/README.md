# Chapter 2: Turn domain work into executable contracts

Open [chapter02.ipynb](chapter02.ipynb) and run all cells in order from a fresh Python kernel. Sections 1 to 9 use the standard library only (Python 3.10 or newer). Section 10 optionally calls a hosted model and skips itself when none is configured.

The notebook loads the domain manifest (Listing 2.1 and 2.2: frozen dataclasses for numeric criteria, prose exclusions, evidence items, and clinical notes; T004 versions 2 and 3, the latter adding "no anticoagulant therapy within 180 days"), round-trips the manifest through a JSON file to show it is data, loads the eight records, three notes, and twelve fixtures behind Table 2.3, runs the numeric evaluator (Listing 2.3), the stand-in note reader (Listing 2.4), the grounding contract around any reader (Listing 2.5), and the validator, preparer, and evaluator (Listing 2.6). It then works the three exercises and, optionally, runs the same notes through a real model.

## Expected results

- Twelve `pass` lines. Model calls are 0 for every refused or version-2 request and 1 for each version-3 request with a note: three refusals never reach a model.
- F09: a note stating warfarin was started on 2026-07-02 makes the exclusion `not met`, citing the note.
- F10: a dated statement of absence makes it `met`.
- F11: a note that mentions the drug without a date is `unknown: statement has no date`.
- F12: a model (`liar`) that invents a quotation is `unknown: quote not found in note`.
- Exercise 2.2 adds a conflicting-readings fixture and a fifth exit to `evaluate`; Exercise 2.3 shows a misreading the contract cannot catch and where the reviewer catches it instead. Final cell: `13 fixtures pass.`

## Optional: a real reader

Section 10 defines `live_reader`, the Chapter 1 adapter with an extraction prompt, and runs four notes through both the stand-in and the live model, printing each proposal and what `ground` said about it, with endpoint and date. Set-up (Databricks Free Edition or any OpenAI-compatible endpoint) is in the Chapter 1 notebook, Section 8. The harness code does not change between readers.

## Run without opening Jupyter

`chapter02.py`, attached inside the book PDF, is Listings 2.1 to 2.6 plus the fixture data as one script: `python3 chapter02.py`. Or execute the notebook headlessly as described in the Chapter 1 README, substituting `chapter-02/chapter02.ipynb`.

The checked-in notebook has cleared outputs. Assertions are development checks; do not run with `python -O`. Nothing here touches a patient system.
