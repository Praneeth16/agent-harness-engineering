"""Record the Chapter 5 tool-text experiment: how tool docs and refusal style change a live planner.

Four arms, two patients, five trials each, under COORDINATOR with a fresh backend per trial.
Each call and each trial's outcome go to runs/ch05_<arm>_<model>.jsonl, which the book replays.

    AHE_LIVE=1 python tools/record_ch05.py [arm ...]      # arms may run in parallel
"""

import sys
import time

from ahe import ch01, ch05, live
from ahe.ch02 import request
from ahe.ch03 import Budget

# A skill that leaves out the archive tool: the same harness with one tool fewer.
NO_ARCHIVE = ch05.Skill("no-archive", "Every Chapter 5 tool except inspect_log.",
                        frozenset({"search_notes", "read_note", "evaluate_rule",
                                   "draft_packet", "submit_packet"}), "")
ARMS = (("terse", False, False, None),               # (name, full docs, teaching errors, skill)
        ("terse_docs_teach_errors", False, True, None),
        ("full", True, True, None),
        ("full_no_inspect", True, True, NO_ARCHIVE))
PATIENTS, TRIALS = ("P042", "P043"), 5
BUDGET = Budget(steps=24, reads=6)


def patient(rec):
    def chat(prompt):
        for attempt in range(3):          # network errors are recorded, then retried
            try:
                return rec(prompt)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5)
    return chat


def main(names):
    assert live.load_env(), "set AHE_LIVE=1 and AHE_API_KEY"
    for arm, full, teach, skill in ARMS:
        if names and arm not in names:
            continue
        rec = live.Recorder(f"ch05_{arm}_gpt-6-luna")
        ch01.chat = patient(rec)
        for pid in PATIENTS:
            for i in range(TRIALS):
                rec.trial = f"{pid}-{i}"
                backend = ch05.Submissions()
                h = ch05.Harness(ch05.COORDINATOR, backend, teach=teach, skill=skill)
                run = h.run(request(pid, 3), ch05.live_planner5(h, full), budget=BUDGET)
                rec.outcome(state=run.state, reason=run.reason, steps=run.count("proposal"),
                            refusals=run.count("gate"), receipt=getattr(run, "receipt", None),
                            backend_calls=backend.calls, stored=len(backend.stored),
                            results={k: v.status for k, v in run.results.items()})
                print(arm, pid, i, run.state, run.count("proposal"), "steps",
                      run.count("gate"), "refused", len(backend.stored), "stored", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
