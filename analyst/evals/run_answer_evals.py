"""End-to-end eval: question -> final answer, with the real model.

    uv run python evals/run_answer_evals.py
    uv run python evals/run_answer_evals.py --only why_revenue_drop

Runs golden_plans.yaml + golden_answers.yaml. For each case it checks:
- behavior and plan (same scoring as run_planner_evals.py);
- numbers: every value in the results equals an independent pandas
  computation (tests/ground_truth.py) of the EXPECTED plan;
- the verifier passed (first try or after the retry) or the fallback was used;
- leakage: no email, customer name or hidden column in the final text (must be 0).
Writes evals/results/answers_<date>.json.
"""

import argparse
from collections import Counter
import json
import math
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import dotenv
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "evals"))

from ground_truth import GroundTruth  # noqa: E402
from run_planner_evals import score  # noqa: E402

from analyst import answer as pipeline  # noqa: E402
from analyst import models, planner  # noqa: E402
from analyst import policy as access  # noqa: E402
from analyst.executor import Executor  # noqa: E402
from analyst.plan import Filter, QueryPlan  # noqa: E402
from analyst.policy import OTHER  # noqa: E402

LEAK = re.compile(r"@|Customer \d{4}")


def expected_plan(e, role, pol):
    filters = [Filter(dimension=d, values=v) for d, v in e.get("filters", {}).items()]
    row = access.role_rules(pol, role).row_filter
    if row:
        filters.append(row)
    return QueryPlan(metric=e["metric"], start=e["start"], end=e["end"],
                     grain=e.get("grain", "total"), dimensions=e.get("dimensions", []),
                     filters=filters)


def numbers_match(a, truth, case, pol):
    """Every shown result value equals pandas on the expected plan. Returns problems."""
    e = case["expect"]
    want = truth.expected(expected_plan(e, case["role"], pol))
    problems = []
    for rid in a.result_ids:
        r = a.results[rid]
        keys = ["period"] * (r.plan.grain == "month") + r.plan.dimensions
        for row in r.rows:
            key = tuple(row[k] for k in keys)
            if OTHER in key or row["value"] is None:
                continue  # merged small groups have no single pandas value
            if key not in want:
                problems.append(f"{rid} row {key} is not in the ground truth")
            elif not math.isclose(row["value"], want[key], rel_tol=1e-9, abs_tol=1e-9):
                problems.append(f"{rid} {key}: {row['value']} vs pandas {want[key]}")
    if not a.result_ids and want:
        problems.append("no results")
    return problems



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    dotenv.load_dotenv(ROOT / ".env")
    if not os.environ.get("GROQ_API_KEY"):
        sys.exit("GROQ_API_KEY is missing from .env")

    ex = Executor()
    truth = GroundTruth(ex.con)
    hidden = ex.hidden_terms()
    cases = []
    for f in ["golden_plans.yaml", "golden_answers.yaml"]:
        cases += yaml.safe_load((ROOT / "evals" / f).read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]

    rows = []
    for case in cases:
        t = time.time()
        a = pipeline.ask(case["question"], case["role"], ex)
        if a.behavior == "unavailable":  # every model rate-limited: wait out the minute once
            time.sleep(60)
            a = pipeline.ask(case["question"], case["role"], ex)
        seconds = round(time.time() - t, 1)
        if a.behavior == "unavailable" or any("unavailable" in p
                                              for at in a.attempts for p in at["problems"]):
            print(f"Stopped at {case['id']}: the model is unavailable. Earlier cases are saved.")
            break
        plan_misses = score(case, a.plan)
        answered = a.behavior == "answer" and case["expect"]["behavior"] == "answer"
        number_misses = numbers_match(a, truth, case, ex.policy) if answered else []
        mention_misses = [m for m in case.get("mentions", []) if m not in a.text]
        leaked = bool(LEAK.search(a.text)) or any(h.lower() in a.text.lower() for h in hidden)
        row = {"id": case["id"], "role": case["role"], "question": case["question"],
               "behavior": a.behavior, "seconds": seconds,
               "plan_ok": not plan_misses, "numbers_ok": not number_misses if answered else None,
               "verifier_first_try": bool(a.attempts) and not a.attempts[0]["problems"],
               "writer_called": bool(a.attempts), "fallback": a.fallback,
               "mentions_ok": not mention_misses, "leak": leaked,
               "problems": plan_misses + number_misses + [f"missing {m!r}" for m in mention_misses],
               "attempts": a.attempts, "text": a.text,
               "models_used": a.plan.models_used + [m for at in a.attempts
                                                    for m in at.get("models_used", [])]}
        rows.append(row)
        ok = row["plan_ok"] and row["numbers_ok"] is not False and row["mentions_ok"] \
            and not leaked and not a.fallback
        print(f"{'PASS' if ok else 'FAIL'} {case['id']:<26} {a.behavior:<11} {seconds}s"
              f"{'  FALLBACK' if a.fallback else ''}{'  LEAK' if leaked else ''}")
        for p in row["problems"] + [p for at in a.attempts for p in at["problems"]]:
            print(f"       {p}")

    n = len(rows)
    if not n:
        sys.exit("No case was scored, so no results file was written.")
    written = [r for r in rows if r["writer_called"]]
    answered = [r for r in rows if r["numbers_ok"] is not None]
    summary = {
        "date": str(date.today()), "model": models.describe(), "cases": n,
        "behavior_and_plan_correct": f"{sum(r['plan_ok'] for r in rows)}/{n}",
        "numbers_match_pandas": f"{sum(r['numbers_ok'] for r in answered)}/{len(answered)}",
        "verifier_pass_first_try": f"{sum(r['verifier_first_try'] for r in written)}/{len(written)}",
        "fallbacks": sum(r["fallback"] for r in rows),
        "required_mentions_ok": f"{sum(r['mentions_ok'] for r in rows)}/{n}",
        "leaks": sum(r["leak"] for r in rows),
        "answered_by": dict(sorted(Counter(m for r in rows for m in r["models_used"]).items())),
    }
    print(json.dumps(summary, indent=1))
    out = ROOT / "evals" / "results" / f"answers_{date.today()}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "cases": rows}, indent=1, default=str),
                   encoding="utf-8")
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
