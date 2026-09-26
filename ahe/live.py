"""Recorded live runs.

The printed `chat` in Chapter 1 is the smallest adapter that works. This
module wraps the same request with the bookkeeping a reported number needs:
model id, date, prompt, raw reply, usage, and cost, appended to a JSONL file
under runs/. Only numbers computed from these records appear in the book.

Settings come from the environment (AHE_BASE_URL, AHE_API_KEY, AHE_MODEL) or
from ~/.config/ahe/openrouter.env. Nothing here runs unless AHE_LIVE=1.
"""

import datetime as dt
import json
import re
import os
import time
import urllib.request
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "runs"
ENV_FILE = Path.home() / ".config" / "ahe" / "openrouter.env"


def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()   # comments; # in a value stays
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and value and key not in os.environ:
                os.environ[key] = value
    return os.environ.get("AHE_LIVE") == "1" and "AHE_API_KEY" in os.environ


class Recorder:
    """A drop-in replacement for ch01.chat that records every call."""

    def __init__(self, experiment, effort="low"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", experiment) or ".." in experiment:
            raise ValueError(f"experiment names are letters, digits, _ . -: {experiment!r}")
        self.experiment = experiment
        self.effort = effort
        self.path = RUNS / f"{experiment}.jsonl"
        self.path.parent.mkdir(exist_ok=True)
        self.trial = None        # set by the experiment driver; links calls to outcomes

    def __call__(self, prompt):
        env = os.environ
        if env.get("AHE_LIVE") != "1":
            raise RuntimeError("set AHE_LIVE=1 to call a model")
        body = {"model": env["AHE_MODEL"],
                "messages": [{"role": "user", "content": prompt}],
                "usage": {"include": True}}
        if self.effort:
            body["reasoning"] = {"effort": self.effort}
        url = env["AHE_BASE_URL"].rstrip("/") + "/chat/completions"
        req = urllib.request.Request(
            url, json.dumps(body).encode(),
            {"Content-Type": "application/json",
             "Authorization": "Bearer " + env["AHE_API_KEY"]})
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=180) as reply:
                out = json.load(reply)
        except Exception as why:          # failed calls are records too
            self._write({"experiment": self.experiment, "at": _now(), "trial": self.trial,
                         "endpoint": url, "requested_model": body["model"],
                         "prompt": prompt, "error": f"{type(why).__name__}: {why}",
                         "seconds": round(time.time() - started, 2)})
            raise
        usage = out.get("usage") if isinstance(out, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        try:
            content = out["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = None
        if not isinstance(content, str):   # unusable: keep the raw reply
            self._write({"experiment": self.experiment, "at": _now(),
                         "trial": self.trial, "endpoint": url,
                         "requested_model": body["model"], "prompt": prompt,
                         "error": "unusable reply", "raw": out,
                         "prompt_tokens": usage.get("prompt_tokens"),
                         "completion_tokens": usage.get("completion_tokens"),
                         "cost_usd": usage.get("cost"),
                         "seconds": round(time.time() - started, 2)})
            return ""
        self._write({
                "experiment": self.experiment,
                "at": _now(),
                "trial": self.trial,
                "endpoint": url,
                "code_version": _code_version(),
                "requested_model": body["model"],
                "served_model": out.get("model"),
                "provider": out.get("provider"),
                "effort": self.effort,
                "prompt": prompt,
                "reply": content,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "cost_usd": usage.get("cost"),
                "seconds": round(time.time() - started, 2),
            })
        return content

    def outcome(self, **fields):
        """Record how a trial ended, so a replay can be checked against it."""
        self._write({"experiment": self.experiment, "at": _now(), "trial": self.trial,
                     "outcome": fields})

    def _write(self, record):
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")


def _code_version():
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(RUNS.parent), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def records(experiment, include_errors=False):
    path = RUNS / f"{experiment}.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows = [r for r in rows if "outcome" not in r]
    return rows if include_errors else [r for r in rows if "error" not in r]


def outcomes(experiment):
    """Terminal outcomes written by the experiment driver, one per trial."""
    path = RUNS / f"{experiment}.jsonl"
    if not path.exists():
        return []
    return [r["outcome"] for r in map(json.loads, path.read_text().splitlines()) if "outcome" in r]


class ReplayMismatch(BaseException):
    """Raised past the runtime's own exception handling, so a bad replay cannot pass as a run."""


def replay(experiment, trials, prompt_of, run_trial):
    """Feed recorded replies back through the runtime, one trial at a time.

    trials: a list of trial descriptions, in the order they were recorded.
    prompt_of(context) rebuilds the prompt a planner would send; it must equal the
    recorded prompt for every call. run_trial(planner, trial) runs one trial and
    returns its Run. A mismatch, a missing reply, a leftover reply, or a failed run
    raises ReplayMismatch. Where the recording holds outcomes, each replayed run's
    state and results must match them too.
    """
    from .ch01 import parse_json
    recs = records(experiment)
    calls = iter(enumerate(recs))

    def planner(context):
        try:
            i, rec = next(calls)
        except StopIteration:
            raise ReplayMismatch(f"{experiment}: ran out of recorded replies") from None
        if prompt_of(context) != rec["prompt"]:
            raise ReplayMismatch(f"{experiment}: call {i} prompt differs from the record")
        try:
            return parse_json(rec["reply"])
        except ValueError:
            return {"tool": None}

    runs = [run_trial(planner, t) for t in trials]
    left = sum(1 for _ in calls)
    if left:
        raise ReplayMismatch(f"{experiment}: {left} recorded replies unused")
    for r in runs:
        if r.state == "failed":
            raise ReplayMismatch(f"{experiment}: replayed run failed: {r.reason}")
    saved = outcomes(experiment)
    if saved:
        if len(saved) != len(runs):
            raise ReplayMismatch(f"{experiment}: {len(saved)} outcomes for {len(runs)} runs")
        for want, r in zip(saved, runs):
            got = {k: v.status for k, v in r.results.items()}
            if (want.get("state"), want.get("results")) != (r.state, got):
                raise ReplayMismatch(f"{experiment}: outcome differs for {want.get('trial', '?')}")
    return runs, recs
