"""Chapter 1: what a domain-specific harness does.

Listings 1.1 to 1.3 of the book are the marked regions below.
"""

# listing 1.1
from copy import deepcopy

RECORDS = {"P017": {"age": 47, "marker": None}}
PROTOCOLS = {("T004", 2): {"age": (18, 65),
                           "marker": (3, 7)}}

def read_record(patient_id):
    return deepcopy(RECORDS[patient_id])  # never the source

def criterion(value, lower, upper):
    if value is None:
        return "unknown"
    if lower <= value <= upper:
        return "met"
    return "not met"

def fake_model(context):
    # Stands in for a model call. Like the assistant in the
    # opening scene, it judges the values it can see and
    # says nothing about the one it cannot.
    record, rules = context["record"], context["rules"]
    seen = {name: criterion(record[name], *bounds)
            for name, bounds in rules.items()
            if record[name] is not None}
    passed = all(s == "met" for s in seen.values())
    verdict = "meets" if passed else "does not meet"
    summary = f"The patient {verdict} the criteria."
    return {"eligible": passed, "criteria": seen,
            "summary": summary}
# end listing

# listing 1.2
def prompt_only(task, model=fake_model):
    context = {"record": read_record(task["patient"]),
               "rules": PROTOCOLS[task["protocol"]]}
    return model(context)  # the answer is the result

def check_claim(claim, results):
    if not isinstance(claim, dict):
        return "claim is not an object"
    named = claim.get("criteria")
    if not isinstance(named, dict):
        return "claim lists no criteria"
    omitted = sorted(set(results) - set(named))
    if omitted:
        return "claim omits " + ", ".join(omitted)
    invented = sorted(set(named) - set(results))
    if invented:
        return "claim invents " + ", ".join(invented)
    wrong = sorted(n for n in results
                   if named[n] != results[n])
    if wrong:
        return "claim contradicts " + ", ".join(wrong)
    if "eligible" not in claim:
        return "claim has no eligible field"
    eligible = claim["eligible"]
    if eligible is not None and type(eligible) is not bool:
        return "eligible is not true, false, or null"
    statuses = set(results.values())
    if eligible is True and statuses != {"met"}:
        return "eligibility claimed without support"
    if eligible is False and "not met" not in statuses:
        return "ineligibility claimed without support"
    return None

def harnessed(task, model=fake_model):
    record = read_record(task["patient"])
    rules = PROTOCOLS[task["protocol"]]
    results = {name: criterion(record[name], *bounds)
               for name, bounds in rules.items()}
    claim = model(deepcopy({"record": record,
                            "rules": rules}))
    open_items = {name: "no value recorded"
                  for name, s in results.items()
                  if s == "unknown"}
    return {"patient": task["patient"],
            "protocol": task["protocol"],
            "criteria": results, "open": open_items,
            "claim_rejected": check_claim(claim, results),
            "review": "pending"}

task = {"patient": "P017", "protocol": ("T004", 2)}
# end listing

# listing 1.3
import json, os, urllib.request

def chat(prompt):
    # Any OpenAI-compatible endpoint: OpenRouter, a
    # Databricks workspace, a local server.
    env = os.environ
    if env.get("AHE_LIVE") != "1":
        raise RuntimeError("set AHE_LIVE=1 to call a model")
    url = env["AHE_BASE_URL"].rstrip("/")
    url += "/chat/completions"
    body = {"model": env["AHE_MODEL"], "messages":
            [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        url, json.dumps(body).encode(),
        {"Content-Type": "application/json",
         "Authorization": "Bearer " + env["AHE_API_KEY"]})
    with urllib.request.urlopen(req, timeout=120) as reply:
        out = json.load(reply)
    try:
        text = out["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""             # an unusable reply
    return text if isinstance(text, str) else ""

def parse_json(text):
    # Models often wrap JSON in prose or a code fence.
    if not isinstance(text, str):
        raise ValueError("reply is not text")
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])

def live_model(context):
    shape = ('{"eligible": true|false|null, "criteria": '
             '{name: "met"|"not met"|"unknown"}, '
             '"summary": text}')
    text = chat("Screen the patient against the protocol. "
                "Reply with JSON only: " + shape + "\n"
                + json.dumps(context))
    try:
        return parse_json(text)
    except ValueError:        # not the JSON we asked for
        return {"criteria": None, "summary": text}
# end listing


if __name__ == "__main__":
    print(prompt_only(task))
    print(harnessed(task))
