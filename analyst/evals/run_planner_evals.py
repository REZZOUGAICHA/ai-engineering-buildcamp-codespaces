"""Run the planner on evals/golden_plans.yaml with the real model and score the plans.

    uv run python evals/run_planner_evals.py               # every case once
    uv run python evals/run_planner_evals.py --repeats 3   # also measures plan stability
    uv run python evals/run_planner_evals.py --only mau_last_month

Needs GROQ_API_KEY in .env. Writes evals/results/planner_<date>.json.
"""

import argparse
from collections import Counter
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import dotenv
import yaml

from analyst import models, planner, semantic
from analyst import policy as access
from analyst.executor import Executor

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden_plans.yaml"
RESULTS = ROOT / "evals" / "results"


def plan_fields(p):
    """The parts of a plan that are compared. Order of dimensions/values doesn't matter."""
    return {"metric": p.metric, "start": str(p.start), "end": str(p.end), "grain": p.grain,
            "dimensions": sorted(p.dimensions),
            "filters": {f.dimension: sorted(f.values) for f in p.filters}}


def expected_fields(e):
    return {"metric": e["metric"], "start": str(e["start"]), "end": str(e["end"]),
            "grain": e.get("grain", "total"), "dimensions": sorted(e.get("dimensions", [])),
            "filters": {d: sorted(v) for d, v in e.get("filters", {}).items()}}


def score(case, result):
    """Return a list of mismatches (empty = pass)."""
    e = case["expect"]
    if result.behavior != e["behavior"]:
        return [f"behavior: got {result.behavior}, expected {e['behavior']}"]
    if e["behavior"] != "answer":
        return []
    if len(result.plans) != 1:
        return [f"expected 1 plan, got {len(result.plans)}"]
    got, want = plan_fields(result.plans[0]), expected_fields(e)
    return [f"{k}: got {got[k]}, expected {want[k]}" for k in want if got[k] != want[k]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between calls (rate limits)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    dotenv.load_dotenv(ROOT / ".env")
    if not os.environ.get("GROQ_API_KEY"):
        sys.exit("GROQ_API_KEY is missing from .env")

    layer = semantic.load()
    pol = access.load(layer)
    ex = Executor(layer, pol)
    cases = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]

    rows = []
    for case in cases:
        runs = []
        for _ in range(args.repeats):
            t = time.time()
            r = planner.plan_question(case["question"], case["role"], layer, pol,
                                      ex.data_from, ex.data_as_of)
            if r.behavior == "unavailable":  # every model rate-limited: wait out the minute once
                time.sleep(60)
                r = planner.plan_question(case["question"], case["role"], layer, pol,
                                          ex.data_from, ex.data_as_of)
            if r.behavior == "unavailable":
                sys.exit(f"Stopped at {case['id']}: {r.message} Nothing was scored for this case.")
            runs.append({"seconds": round(time.time() - t, 1), "misses": score(case, r),
                         "behavior": r.behavior, "tool_calls": r.tool_calls,
                         "plans": [plan_fields(p) for p in r.plans], "message": r.message,
                         "assumptions": r.assumptions, "models_used": r.models_used})
            time.sleep(args.pause)
        signatures = {json.dumps([x["behavior"], x["plans"]], sort_keys=True) for x in runs}
        row = {"id": case["id"], "role": case["role"], "question": case["question"],
               "passes": sum(not x["misses"] for x in runs), "repeats": args.repeats,
               "stable": len(signatures) == 1, "runs": runs}
        rows.append(row)
        mark = "PASS" if row["passes"] == args.repeats else "FAIL"
        print(f"{mark} {case['id']:<26} {row['passes']}/{args.repeats}"
              f"{'' if row['stable'] else '  (UNSTABLE)'}  {runs[0]['seconds']}s")
        for m in sorted({m for x in runs for m in x["misses"]}):
            print(f"       {m}")

    total = sum(r["repeats"] for r in rows)
    passed = sum(r["passes"] for r in rows)
    summary = {"date": str(date.today()), "model": models.describe(),
               "cases": len(rows), "repeats": args.repeats,
               "plan_accuracy": f"{passed}/{total}",
               "cases_all_runs_pass": sum(r["passes"] == r["repeats"] for r in rows),
               "stable_cases": sum(r["stable"] for r in rows),
               "answered_by": dict(sorted(Counter(m for r in rows for x in r["runs"]
                                                  for m in x["models_used"]).items()))}
    print(json.dumps(summary, indent=1))
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"planner_{date.today()}.json"
    out.write_text(json.dumps({"summary": summary, "cases": rows}, indent=1), encoding="utf-8")
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
