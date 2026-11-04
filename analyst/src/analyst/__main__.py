"""Ask one question from the terminal:

    uv run python -m analyst "Why did revenue drop in March 2026?" --role ceo
    uv run python -m analyst "..." --role eu_manager --debug    # also show plan and drafts
"""

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import dotenv

from analyst.answer import ask
from analyst.executor import Executor


def main():
    ap = argparse.ArgumentParser(description="Ask the rule-aware analyst a question.")
    ap.add_argument("question")
    ap.add_argument("--role", required=True, help="ceo, eu_manager, finance_analyst, product_manager")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    dotenv.load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    a = ask(args.question, args.role, Executor())
    print(a.text)
    if args.debug:
        print("\n--- plan ---\n" + a.plan.model_dump_json(indent=1))
        for r in a.results.values():
            print(f"\n--- {r.result_id} SQL ---\n{r.sql}\nparams: {r.params}")
        print("\n--- writer attempts ---\n" + json.dumps(a.attempts, indent=1))


if __name__ == "__main__":
    main()
