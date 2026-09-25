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
import os
import time
import urllib.request
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "runs"
ENV_FILE = Path.home() / ".config" / "ahe" / "openrouter.env"


def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if key and value and key not in os.environ:
                os.environ[key.strip()] = value.strip()
    return os.environ.get("AHE_LIVE") == "1" and "AHE_API_KEY" in os.environ


class Recorder:
    """A drop-in replacement for ch01.chat that records every call."""

    def __init__(self, experiment, effort="low"):
        self.experiment = experiment
        self.effort = effort
        self.path = RUNS / f"{experiment}.jsonl"
        self.path.parent.mkdir(exist_ok=True)

    def __call__(self, prompt):
        env = os.environ
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
        with urllib.request.urlopen(req, timeout=180) as reply:
            out = json.load(reply)
        content = out["choices"][0]["message"]["content"]
        usage = out.get("usage", {})
        with self.path.open("a") as f:
            f.write(json.dumps({
                "experiment": self.experiment,
                "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
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
            }) + "\n")
        return content


def records(experiment):
    path = RUNS / f"{experiment}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]
