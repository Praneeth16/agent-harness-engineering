"""Build the chapter notebooks from the ahe package.

The notebooks hold no copy of the runtime. Each one imports ahe.chNN, runs the
chapter's examples and fixtures, replays the recorded live runs from runs/,
and gives the exercise solutions. Live calls happen only with AHE_LIVE=1.

    python tools/build_notebooks.py        # writes chapter-0N/chapter0N.ipynb
"""

from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]

SETUP = '''import sys, pathlib
root = pathlib.Path.cwd()
if not (root / "ahe").exists():
    root = root.parent            # opened from inside chapter-0N/
sys.path.insert(0, str(root))
assert sys.version_info >= (3, 10), "Use Python 3.10 or newer."
import os
LIVE = os.environ.get("AHE_LIVE") == "1"
print("Python", sys.version.split()[0], "| live calls:", "on" if LIVE else "off")'''

LIVE_NOTE = """### Live calls

Every live cell below does nothing unless `AHE_LIVE=1` is set before Jupyter starts. Settings come from the environment or from `~/.config/ahe/openrouter.env`:

```
AHE_BASE_URL=https://openrouter.ai/api/v1     # or https://<workspace>/serving-endpoints
AHE_API_KEY=...                                # never commit this file
AHE_MODEL=openai/gpt-6-luna
```

Any OpenAI-compatible endpoint works, including a Databricks workspace (base URL `https://<workspace-host>/serving-endpoints`, a personal access token as the key, and a served model name). `ahe.live.Recorder` replaces `chat` so that each call is appended to `runs/<experiment>.jsonl` with its prompt, reply, token counts, and cost. Use a new experiment name for your own runs so the book's records stay as they are."""


def md(text):
    return nbf.v4.new_markdown_cell(text.strip())


def code(text):
    return nbf.v4.new_code_cell(text.strip())


def notebook(title, intro, cells):
    nb = nbf.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nb.cells = [md(f"# {title}\n\n{intro}"), md("## Setup"), code(SETUP)] + cells
    for i, cell in enumerate(nb.cells):
        cell.id = f"cell-{i:02d}"     # stable ids, so regenerating changes nothing
    return nb


CH1 = notebook(
    "Chapter 1: What a domain-specific harness does",
    "Companion to *Agent Harness Engineering*. Run all cells in order from a fresh kernel. "
    "Listings 1.1 to 1.3 live in `ahe/ch01.py`; this notebook runs them, replays the recorded "
    "live trials, and gives the exercise solutions. All patients, protocols, and thresholds are fictional.",
    [
        md("## 1. The two paths (Listings 1.1 and 1.2)"),
        code('''from pprint import pprint
from ahe import ch01
from ahe.ch01 import task, prompt_only, harnessed, check_claim, criterion, fake_model
pprint(prompt_only(task))
pprint(harnessed(task))'''),
        md("## 2. What `check_claim` rejects\n\nEach hostile claim below is rejected with a named reason. The tests in `tests/test_ch01_04.py` assert the same cases."),
        code('''results = {"age": "met", "marker": "unknown"}
claims = {
    "omits marker": {"eligible": True, "criteria": {"age": "met"}},
    "contradicts": {"eligible": None, "criteria": {"age": "met", "marker": "met"}},
    "invents a rule": {"eligible": None, "criteria": {**results, "extra": "met"}},
    "integer eligible": {"eligible": 1, "criteria": results},
    "string eligible": {"eligible": "true", "criteria": results},
    "no eligible field": {"criteria": results},
    "unsupported no": {"eligible": False, "criteria": results},
    "honest": {"eligible": None, "criteria": results},
}
for name, claim in claims.items():
    print(f"{name:18s} -> {check_claim(claim, results)}")'''),
        md("## 3. The recorded live trials (Table 1.3)\n\nReplaying `check_claim` over the recorded replies reproduces the table without a key."),
        code('''from collections import Counter
from ahe import live
conditions = {"plain": "ch01_p017_gpt-6-luna", "deadline": "ch01_p017_deadline_gpt-6-luna",
              "forced answer": "ch01_p017_forced_gpt-6-luna", "both": "ch01_p017_pressure_gpt-6-luna"}
for name, exp in conditions.items():
    recs = live.records(exp)
    verdicts = Counter()
    for r in recs:
        try:
            claim = ch01.parse_json(r["reply"])
        except ValueError:
            claim = {"criteria": None}
        verdicts[check_claim(claim, results)] += 1
    cost = sum(r["cost_usd"] or 0 for r in recs)
    print(f"{name:14s} n={len(recs)} model={recs[0]['served_model']} cost=${cost:.4f} {dict(verdicts)}")'''),
        md(LIVE_NOTE),
        code('''if LIVE:
    live.load_env()
    rec = live.Recorder("my_ch01_forced")          # your own experiment name
    prefix = "Answer eligible with true or false. Do not answer null. "
    ch01.chat = lambda prompt: rec(prefix + prompt)
    for i in range(10):
        print(harnessed(task, model=ch01.live_model)["claim_rejected"])
else:
    print("Set AHE_LIVE=1 to run the forced-answer condition against your model.")'''),
        md("## Exercise solutions\n\n### Exercise 1.1\n\nThe endpoints 3 and 7 are included, so both are `met`; 2 and 9 are `not met`. `fake_model` now sees a marker value, so its claim lists both criteria. For 3 and 7 it claims eligibility with every criterion met, and the check accepts it. For 2 and 9 it claims ineligibility with one criterion not met, and the check accepts that too. An accepted packet still leaves the decision, and anything the protocol does not cover, to the reviewer."),
        code('''saved = ch01.RECORDS["P017"]["marker"]
for value in (3, 7, 2, 9):
    ch01.RECORDS["P017"]["marker"] = value
    p = harnessed(task)
    print(value, p["criteria"], "| claim rejected:", p["claim_rejected"])
ch01.RECORDS["P017"]["marker"] = saved'''),
        md("### Exercise 1.2\n\nAgreement fires first after coverage: the claim says the marker is `met`, the code computed `unknown`. The harness trusts `results` because they come from the record through code a test can check; the model's `criteria` are a claim about the record."),
        code('''def overclaims(context):
    return {"eligible": True, "criteria": {"age": "met", "marker": "met"}}
print(harnessed(task, model=overclaims)["claim_rejected"])'''),
        md("### Exercise 1.3\n\nRun the live cell above with your own model and experiment name, then summarize it with `live.records(\"my_ch01_forced\")` as in section 3. Record the model id and date in your ledger row.\n\n### Exercise 1.4\n\nA suitable contract: prepare a packet for P017 against T004 version 2 with one status for every criterion in the selected version. `met` and `not met` must follow from a recorded value in the protocol's unit; a missing value is `unknown` with the reason. The packet may complete with open items. It never states eligibility, which stays with the reviewer. A fixed workflow is enough while the evidence is structured fields; an agent loop becomes worth its cost when the evidence is spread over free text whose useful searches cannot be listed in advance."),
    ])


CH2 = notebook(
    "Chapter 2: Turn domain work into executable contracts",
    "Companion to *Agent Harness Engineering*. Listings 2.1 to 2.7 live in `ahe/ch02.py`.",
    [
        md("## 1. The fixtures (Table 2.5)"),
        code('''from ahe import ch02
from ahe.ch02 import FIXTURES, run_fixtures, prepare, request, ground, NOTES
failures = run_fixtures(FIXTURES)
assert failures == {}, failures
print(len(FIXTURES), "fixtures pass.")'''),
        md("## 2. A packet, with the quotation the reviewer sees"),
        code('''from pprint import pprint
pprint(prepare(request("P036", 3)))'''),
        md("## 3. The hostile readers against `ground`"),
        code('''from datetime import date
on = date(2026, 9, 1)
note = NOTES["P036"][0]
for reader in (ch02.fake_reader, ch02.liar, ch02.forger, ch02.drifter, ch02.redater,
               ch02.flipper, ch02.fragment, ch02.hider, ch02.garbler):
    proposal = reader({"note": note.text, "field": "anticoagulant"})
    print(f"{reader.__name__:12s} -> {ground(proposal, note, 'anticoagulant', on) or 'grounded'}")'''),
        md("## 4. The recorded live reader (Table 2.2)"),
        code('''from collections import Counter
from ahe import ch01, live
by_text = {n.text: (pid, n) for pid, ns in NOTES.items() for n in ns}
recs = live.records("ch02_reader_gpt-6-luna")
tally = Counter()
for r in recs:
    pid, n = by_text[r["prompt"].split("Note: ", 1)[1]]
    try:
        p = ch01.parse_json(r["reply"])
    except ValueError:
        p = {}
    tally[(pid, p.get("status"), ground(p, n, "anticoagulant", date(2026, 9, 1)) or "grounded")] += 1
for k, v in sorted(tally.items()):
    print(k, v)
print(len(recs), "calls; cost $%.4f" % sum(r["cost_usd"] for r in recs))'''),
        md(LIVE_NOTE),
        code('''if LIVE:
    live.load_env()
    ch01.chat = live.Recorder("my_ch02_reader")
    for pid in ("P036", "P037", "P038", "P039"):
        got = prepare(request(pid, 3), model=ch02.live_reader)
        print(pid, got["criteria"]["no_anticoag"])
else:
    print("Set AHE_LIVE=1 to run the live reader.")'''),
        md("## Exercise solutions\n\n### Exercise 2.1\n\nVersion 2 has no freshness limit, so the June 2025 reading is `met`; version 3 limits readings to 90 days, so the same reading is `unknown`, stale. The protocol contract changed, not the evidence. The packet names the version, and the reason names the reading's date, so two packets a month apart cannot be confused."),
        code('''for v in (2, 3):
    print(v, prepare(request("P023", v))["criteria"]["marker"])'''),
        md("### Exercise 2.2\n\nThe coordinator would not pick one: two fresh readings that disagree on the status are `unknown`, and the reason names both. Write the fixture first, then change `evaluate`."),
        code('''from datetime import date
from ahe.ch02 import Evidence, Result, evaluate, finite, PROTOCOLS

def evaluate_2_2(rule, evidence, on):
    result = evaluate(rule, evidence, on)
    if result.status == "unknown":
        return result          # missing, stale, not a number, or wrong unit
    fresh = [e for e in evidence if e.field == rule.field and e.unit == rule.unit
             and finite(e.value) and e.observed <= on and (rule.max_age_days is None
                                       or (on - e.observed).days <= rule.max_age_days)]
    statuses = {rule.lower <= e.value <= rule.upper for e in fresh}
    if len(statuses) > 1:
        return Result("unknown", "fresh readings disagree: "
                      + ", ".join(f"{e.value} on {e.observed}" for e in fresh))
    return result

marker = PROTOCOLS[1].rules[1]
readings = [Evidence("x1", "marker", 5, "ng/mL", date(2026, 8, 20)),
            Evidence("x2", "marker", 9, "ng/mL", date(2026, 8, 25))]
print(evaluate(marker, readings, date(2026, 9, 1)))
print(evaluate_2_2(marker, readings, date(2026, 9, 1)))
broken = [Evidence("x3", "marker", None, "ng/mL", date(2026, 8, 20)), readings[0]]
print(evaluate_2_2(marker, broken, date(2026, 9, 1)))   # unknown: not a number'''),
        md("### Exercise 2.3\n\nThe stand-in reader sees `warfarin` and a date and reports `present`; the quotation and date are both in the note, so `ground` accepts it and the rule comes out `not met`. The packet's quotation is what lets the reviewer catch the error. Measuring how often it happens needs notes labeled by a person, which Chapter 9 builds."),
        code('''from ahe.ch02 import Note, read_note
tricky = Note("n99", date(2026, 8, 1),
              "Warfarin was considered on 2026-08-01 and rejected because of bleeding risk.")
print(read_note(ch02.fake_reader, tricky, "anticoagulant", date(2026, 9, 1))[:2])'''),
    ])


CH3 = notebook(
    "Chapter 3: Build the smallest useful runtime",
    "Companion to *Agent Harness Engineering*. Listings 3.1 to 3.8 live in `ahe/ch03.py`.",
    [
        md("## 1. Fixed program versus planner (Table 3.1)"),
        code('''from ahe import ch03
from ahe.ch03 import request, run_task, fixed_screen, fake_planner, packet
for pid in ("P041", "P043"):
    fixed = fixed_screen(request(pid, 3))
    planned = run_task(request(pid, 3), fake_planner)
    print(pid, "fixed:", fixed.results["no_anticoag"].status, "| planner:",
          planned.results["no_anticoag"].status, "reads", planned.reads)'''),
        md("## 2. Misbehaving planners (Table 3.3)"),
        code('''for row in ch03.trials(request("P041", 3)):
    print(f"{row[0]:14s} {row[1]:10s} {row[2][:40]:40s} steps={row[3]:2d} reads={row[4]}")'''),
        md("## 3. One run's raw events"),
        code('''run = run_task(request("P041", 3), fake_planner)
for e in run.events:
    print(e)
print(packet(run))'''),
        md("## 4. Replay the recorded live-planner runs (Table 3.5)\n\nThe runtime is deterministic given the planner's replies, so feeding the recorded replies back reproduces each run exactly."),
        code('''from ahe import ch01, live
recs = live.records("ch03_planner_gpt-6-luna")
replies = iter(r["reply"] for r in recs)
def replay(context):
    try:
        return ch01.parse_json(next(replies))
    except ValueError:
        return {"tool": None}
for pid in ["P041"] * 5 + ["P043"] * 5:
    r = run_task(request(pid, 3), replay)
    print(pid, r.state, r.count("proposal"), "steps,", r.reads, "reads,",
          r.results.get("no_anticoag", ("-",))[0])'''),
        md(LIVE_NOTE),
        code('''if LIVE:
    live.load_env()
    ch01.chat = live.Recorder("my_ch03_planner")
    r = run_task(request("P043", 3), ch03.live_planner)
    for e in r.events:
        if e["kind"] == "proposal":
            print(e["proposal"])
    print(r.state, r.reason)
else:
    print("Set AHE_LIVE=1 to run the live planner.")'''),
        md("## Exercise solutions\n\n### Exercise 3.1\n\nA rule: when the step budget is spent and `unfinished(run)` is empty, complete the run with the reason \"budget spent; every rule current\". It is wrong when finishing is itself a decision the planner owes, for example when a later chapter requires a drafted packet or an approval before completion."),
        code('''def drive_autofinish(run, planner, reader=ch03.fake_reader):
    run = ch03.drive(run, planner, reader)
    if run.state == "blocked" and run.reason == "step budget spent" and not ch03.unfinished(run):
        run.state, run.reason = "completed", "budget spent; every rule current"
        run.record("stop", state=run.state, reason=run.reason)
    return run'''),
        md("### Exercise 3.2\n\nThe gate needs `draft_packet` in `SPEC` with no arguments. The completion check then also requires that a draft exists and that it was drafted after the last change to any rule's evidence."),
        code('''def t_draft(run):
    run.draft = packet(run)
    run.draft_seq = len(run.events)
    return {"drafted": sorted(run.draft["criteria"])}
ch03.TOOLS["draft_packet"] = t_draft
ch03.SPEC["draft_packet"] = {}
print(ch03.gate(ch03.start(request("P041", 3)), {"tool": "draft_packet", "args": {}}))
del ch03.TOOLS["draft_packet"], ch03.SPEC["draft_packet"]'''),
        md("### Exercise 3.3\n\nRead the proposals the replay prints in section 4. Letting the model choose search terms unwatched is reasonable, because the gate bounds them and `ground` checks what they find. Stopping early is the choice to fixture first: 2 of the 10 recorded runs spent their budget with every rule current."),
    ])


CH4 = notebook(
    "Chapter 4: Engineer the context layer",
    "Companion to *Agent Harness Engineering*. Listings 4.1 to 4.4 live in `ahe/ch04.py`.",
    [
        md("## 1. The exit test (Table 4.7)"),
        code('''from ahe import ch03, ch04
from ahe.ch03 import request, run_task, Budget
run, mid = ch04.table_4_3()
print("naive:", ch04.size(ch04.naive_context(run)), "chars")
for row in ch04.exit_test(mid, ch04.P042_AFTER_3, (2400, 1200, 900, 500)):
    print(row)'''),
        md("## 2. What the model saw at one step"),
        code('''from pprint import pprint
run = run_task(request("P042", 3), ch04.planner_v2, context=ch04.context_v2)
ctx = [e for e in run.events if e["kind"] == "context"][3]
print(ctx["sha"], ctx["chars"], "chars")
pprint(ctx["bundle"]["pending"])'''),
        md("## 3. Replay the four-context experiment (Table 4.4)"),
        code('''from ahe import ch01, live
def replay(experiment, pid, context, n=5):
    replies = iter(r["reply"] for r in live.records(experiment))
    def planner(c):
        try:
            return ch01.parse_json(next(replies))
        except ValueError:
            return {"tool": None}
    return [run_task(request(pid, 3), planner, budget=Budget(steps=16, reads=6), context=context)
            for _ in range(n)]
for pid in ("P042", "P043"):
    for tag, context in (("naive", ch03.context_for), ("assembled", ch04.context_without_terms),
                         ("terms", ch04.context_v2),
                         ("historyterms", ch04.context_history_with_terms)):
        runs = replay(f"ch04_{pid.lower()}_{tag}_gpt-6-luna", pid, context)
        done = sum(r.state == "completed" for r in runs)
        print(pid, f"{tag:10s} completed {done}/5, steps", [r.count("proposal") for r in runs])'''),
        md(LIVE_NOTE),
        code('''if LIVE:
    live.load_env()
    ch01.chat = live.Recorder("my_ch04_terms")
    r = run_task(request("P042", 3), ch03.live_planner, context=ch04.context_v2,
                 budget=Budget(steps=16, reads=6))
    print(r.state, r.reason, r.count("proposal"), "steps")
else:
    print("Set AHE_LIVE=1 to run the live planner on the assembled context.")'''),
        md("## Exercise solutions\n\n### Exercise 4.1\n\nThe run fails with a named reason, because `fake_planner` declares context version 1. An adapter can map `recent` to `observations`, but it cannot fill `observations` honestly: version 1 promised every observation, and the window holds only the last few. The adapter should say so, for example by adding the archive count, rather than pretend the list is complete."),
        code('''r = run_task(request("P042", 3), ch03.fake_planner, context=ch04.context_v2)
print(r.state, r.reason)

def as_v1(bundle):
    return {"task": bundle["task"], "rules": [x["id"] for x in bundle["rules"]],
            "results": {k: v["status"] for k, v in bundle["results"].items()},
            "observations": bundle["recent"], "steps_left": bundle["steps_left"],
            "observations_omitted": bundle["archive"]["observations"] - len(bundle["recent"])}
def adapted(context):
    return ch03.fake_planner(as_v1(context))
adapted.context_schema = 2
r = run_task(request("P042", 3), adapted, context=ch04.context_v2)
print(r.state, r.reason)'''),
        md("### Exercise 4.2\n\nThe invariant: no note sentence appears in the bundle unless it is a grounded quotation in `results`. Returning the whole note from `t_read` puts every sentence into observations and so into the window. The reviewer would see the same packet; the log would show the planner being handed text no contract checked."),
        code('''import json
from ahe.ch02 import sentences
def no_ungrounded_text(run, ctx):
    quotes = {v["quote"] for v in ctx["results"].values() if v.get("quote")}
    text = json.dumps(ctx["recent"], default=str)
    return not any(s in text for n in ch04.NOTES[run.request["patient"]]
                   for s in sentences(n.text) if s not in quotes and len(s) > 20)
run = run_task(request("P042", 3), ch04.planner_v2, context=ch04.context_v2)
print(all(no_ungrounded_text(run, e["bundle"]) for e in run.events if e["kind"] == "context"))'''),
        md("### Exercise 4.3\n\nHanded the note, the stand-in reader grounds it as `present`, so the reader is not the problem. Lexical search with the listed terms never returns it, and the rule stays `unknown`. A fixture that justifies vector search states that n70 is relevant, that a search for anticoagulants must return it, and that `ground` must still find the quotation and date in the note, because a similarity match is not a reading."),
        code('''from datetime import date
from ahe.ch02 import read_note, fake_reader
print(read_note(fake_reader, ch04.NOTES["P044"][0], "anticoagulant", date(2026, 9, 1))[:2])
r = run_task(request("P044", 3), ch04.planner_v2, context=ch04.context_v2)
print(r.hits, r.results["no_anticoag"])'''),
    ])


if __name__ == "__main__":
    for n, nb in enumerate((CH1, CH2, CH3, CH4), start=1):
        path = ROOT / f"chapter-0{n}" / f"chapter0{n}.ipynb"
        path.parent.mkdir(exist_ok=True)
        nbf.write(nb, path)
        print("wrote", path.relative_to(ROOT))
