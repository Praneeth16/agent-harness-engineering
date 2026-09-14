# Chapter 1: What a domain-specific harness does

Open [chapter01.ipynb](chapter01.ipynb) and run all cells in order from a fresh Python kernel.

The notebook reproduces Listing 1.1 from the book, then expands its boundary and coverage exercises. It distinguishes missing evidence from a failed criterion and shows a completed packet whose human review is still pending.

## Expected results

- The deliberately faulty baseline prints `True` despite the missing marker.
- The corrected marker result is `unknown`. Boundary values 3 and 7 are `met`.
- Incomplete criterion sets produce caught, expected rejections. No notebook cell should end with an unhandled exception.
- The packet has `preparation_status: complete`, `review_status: pending`, and `eligibility: not_determined`.

Keep the baseline fixture unchanged. Use the independent test cases to try other values. The notebook assumes finite numeric inputs in agreed units, with `None` for missing evidence. Its coverage function only checks criterion names. It is not a full evidence validator.

## Execute without opening Jupyter

Install the notebook tools using the root README, then run this from the repository root:

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

Alternatively, use JupyterLab's Restart Kernel and Run All command. The checked-in notebook has cleared outputs so each reader runs the examples themselves.

Assertions in the exercises are development checks. Do not run verification with Python optimization (`-O`), which disables assertions. The example does not call a model or access patient systems.

## Verification

Validated with Python 3.12.14 and nbformat 5.11.1. All seven code cells executed in order in a fresh in-process IPython kernel. This environment prevents local socket binding, so a separate Jupyter server and the socket-based headless command were not exercised here. The notebook contains no frontend-specific code or widgets.
