"""Ask the analyst from the terminal.

    uv run python -m analyst "Why did revenue drop in March 2026?" --role ceo
    uv run python -m analyst "..." --role eu_manager --debug    # also show plan and drafts
    uv run python -m analyst --role ceo                         # a chat session

With LOGFIRE_TOKEN in .env, every question is sent to Logfire as a trace. A chat
session is one trace: one `analyst_session` span with each question under it.
"""

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import dotenv
import logfire

from analyst import trace
from analyst.answer import ask
from analyst.executor import Executor


def cost_line(t):
    cost = "unknown price" if t["cost_usd"] is None else f"${t['cost_usd']:.6f}"
    return f"{t['input_tokens']} in / {t['output_tokens']} out tokens, {cost}"


def show(a, debug):
    print(a.text)
    print(f"\n[answer {a.answer_id}: {cost_line(trace.totals(a.usage))}]")
    if debug:
        print("\n--- plan ---\n" + a.plan.model_dump_json(indent=1))
        for r in a.results.values():
            print(f"\n--- {r.result_id} SQL ---\n{r.sql}\nparams: {r.params}")
        print("\n--- writer attempts ---\n" + json.dumps(a.attempts, indent=1))


def chat(role, executor, debug):
    """One session span; each question is attached to its context, so the whole
    conversation is one tree (the course's get_context / attach_context pattern)."""
    usage = []
    with logfire.span("analyst_session", role=role) as session:
        ctx = logfire.get_context()
    print(f"Analyst session as {role}. Empty line to quit.")
    while (question := input("\n> ").strip()):
        with logfire.attach_context(ctx):
            a = ask(question, role, executor)
            show(a, debug)
            usage += a.usage
            if a.behavior == "answer":
                fb = input("Useful? [y/n, Enter to skip] ").strip().lower()
                if fb in ("y", "n"):
                    logfire.info("user_feedback", feedback=1 if fb == "y" else -1,
                                 answer_id=a.answer_id, role=role)
    t = trace.totals(usage)
    with logfire.attach_context(ctx):
        logfire.info("session_end", **t)
    print(f"\n[session: {cost_line(t)}]")
    return session


def main():
    ap = argparse.ArgumentParser(description="Ask the rule-aware analyst a question.")
    ap.add_argument("question", nargs="?", help="leave out to start a chat session")
    ap.add_argument("--role", required=True, help="ceo, eu_manager, finance_analyst, product_manager")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    dotenv.load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    trace.setup()
    executor = Executor()
    if args.question:
        show(ask(args.question, args.role, executor), args.debug)
    else:
        chat(args.role, executor, args.debug)


if __name__ == "__main__":
    main()
