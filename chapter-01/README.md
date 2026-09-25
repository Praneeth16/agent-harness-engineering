# Chapter 1: What a domain-specific harness does

Open [chapter01.ipynb](chapter01.ipynb) and run all cells in order from a fresh Python kernel.

The notebook runs Listings 1.1 and 1.2 from the book, works the chapter's three exercises with solutions, and then (optionally) runs Listing 1.3, which points the same harness at a real hosted model. Listing 1.1 holds the fixture (patient P017, protocol T004 version 2), the `criterion` rule, and `fake_model`, a deterministic stand-in for a model call that judges only the values it can see. Listing 1.2 runs the same request two ways: `prompt_only` accepts the model's answer as the result; `harnessed` computes every criterion in code and rejects a claim that omits a criterion or that the evidence cannot support.

## Expected results

- `prompt_only` returns `eligible: True` with a single criterion, even though marker M is missing.
- `harnessed` returns both criteria, `marker: 'unknown'`, `eligible: None`, `rejected: 'claim omits marker'`, and `review: 'pending'`.
- Exercise 1.1: marker values 3 and 7 are met (endpoints included) and the claim is accepted; 2 and 9 are not met and the supported negative claim is accepted. Review stays pending in every case.
- Exercise 1.2: a model that asserts `marker: met` without evidence is rejected with `eligibility claimed without support`.
- A model that says `eligible: false` while the marker is merely unknown is rejected with `ineligibility claimed without support`.
- No cell ends with an unhandled exception. Section 8's live cells skip themselves when no model is configured.

The example assumes finite numeric inputs in agreed units, with `None` for missing evidence. It checks neither types, dates, nor sources; Chapter 2 adds those.

## Optional: run against a real model

Section 8 of the notebook is a step-by-step guide to getting a hosted model at no cost through Databricks Free Edition, running the notebook either inside that workspace (credentials are automatic) or locally with `DATABRICKS_HOST` and `DATABRICKS_TOKEN` set, and then running the P017 request repeatedly to count how often the harness has to reject a claim. The default endpoint is `databricks-gpt-5-6-luna`; set `MODEL_ENDPOINT` to use another. The adapter speaks the OpenAI-compatible chat interface, so other providers work with a base-URL change.

Free Edition is for non-commercial use and has fair-usage quotas. The live cells print the endpoint name and the date with every tally so that results can be quoted honestly. No live numbers are printed in the book; whatever your run shows is what it shows.

## Run without opening Jupyter

The two listings together are a complete script. Copy them into `chapter01.py` and run `python3 chapter01.py`, or execute the notebook headlessly from the repository root after installing the tools listed in the root README:

```bash
python - <<'PYTHON'
import nbformat
from nbclient import NotebookClient

notebook = nbformat.read("chapter-01/chapter01.ipynb", as_version=4)
nbformat.validate(notebook)
NotebookClient(notebook, timeout=60, kernel_name="python3").execute()
print("All Chapter 1 cells passed.")
PYTHON
```

The checked-in notebook has cleared outputs so that each reader runs the examples. Assertions in the exercises are development checks; do not run with `python -O`, which disables them. Sections 1 to 7 call no model; Section 8 calls only the endpoint you configure. Nothing touches a patient system.
