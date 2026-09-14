# Agent Harness Engineering

Companion notebooks for **Agent Harness Engineering: Engineering Reliable Domain-Specific Agent Harnesses** by Praneeth Paikray.

Each chapter has its own folder. Chapter 1 is available; later chapter folders will be added with their implementations. The repository contains code and README files. The manuscript, cover artwork, and PDF are distributed separately.

| Chapter | Topic | Notebook |
| --- | --- | --- |
| 1 | What a domain-specific harness does | [Open Chapter 1](chapter-01/chapter01.ipynb) |

## Run locally

Use Python 3.10 or newer. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install jupyterlab nbclient nbformat ipykernel
jupyter lab chapter-01/chapter01.ipynb
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead. Select the Python kernel from this environment, then restart the kernel and run all cells. The chapter uses only the Python standard library. No API key, external data, or network connection is needed during notebook execution.

See the [Chapter 1 README](chapter-01/README.md) for expected results and headless verification.

## Scope

The HLS example uses fictional patient records and invented laboratory thresholds. It demonstrates software behavior and has no clinical meaning. The notebooks do not make enrollment decisions. Chapter 1 is a deterministic exercise, not a language-model benchmark or a production harness.
