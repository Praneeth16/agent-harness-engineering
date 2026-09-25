# Agent Harness Engineering

Companion notebooks for **Agent Harness Engineering: Engineering Reliable Domain-Specific Agent Harnesses** by Praneeth Paikray.

Each chapter has its own folder. Chapters 1 to 4 are available; later chapter folders will be added with their implementations. The repository contains code and README files. The manuscript, cover artwork, and PDF are distributed separately.

| Chapter | Topic | Notebook |
| --- | --- | --- |
| 1 | What a domain-specific harness does | [Open Chapter 1](chapter-01/chapter01.ipynb) |
| 2 | Turn domain work into executable contracts | [Open Chapter 2](chapter-02/chapter02.ipynb) |
| 3 | Build the smallest useful runtime | [Open Chapter 3](chapter-03/chapter03.ipynb) |
| 4 | Engineer the context layer | [Open Chapter 4](chapter-04/chapter04.ipynb) |

## Run locally

Use Python 3.10 or newer. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install jupyterlab nbclient nbformat ipykernel
jupyter lab chapter-01/chapter01.ipynb
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead. Select the Python kernel from this environment, then restart the kernel and run all cells. The chapter uses only the Python standard library. No API key, external data, or network connection is needed during notebook execution.

See the chapter READMEs ([1](chapter-01/README.md), [2](chapter-02/README.md), [3](chapter-03/README.md), [4](chapter-04/README.md)) for expected results and headless verification.

## Optional live model

Each chapter's last section points the harness at a real model: Chapter 1 as judge, Chapter 2 as note reader, Chapter 3 as planner, Chapter 4 as planner on an assembled, size-limited bundle. The notebook explains a no-cost route through Databricks Free Edition and how to run either inside that workspace or locally with `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, and optionally `MODEL_ENDPOINT` set in the environment. The adapter uses only the standard library and the OpenAI-compatible chat interface. Skip the section and everything else still runs.

## Scope

The clinical-trial example uses fictional patient records, protocol versions, dates, and thresholds. It shows how software behaves and has no clinical meaning; the notebooks never make enrollment decisions. Chapter 1 uses a deterministic stand-in for the model so that its failure repeats on every run. It reproduces a failure mode, not a measurement of any real model.
