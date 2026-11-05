"""Monitoring with Pydantic Logfire, plus token and cost counting.

setup() is called by the entry points (CLI, app), not on import, so pytest and
the evals never send anything. With no LOGFIRE_TOKEN in the environment,
nothing leaves the machine either way ("if-token-present").

Traces go to a third party, so they count as output for the leak rule: span
attributes only hold data the role was already allowed to see.
"""

from pathlib import Path

import logfire
import yaml
from pydantic_ai.messages import ModelResponse

PRICES_FILE = Path(__file__).resolve().parents[2] / "config" / "prices.yaml"
_prices = None


def setup():
    logfire.configure(send_to_logfire="if-token-present", service_name="analyst",
                      console=False)
    logfire.instrument_pydantic_ai()  # every planner and writer LLM call and tool call


def prices():
    global _prices
    if _prices is None:
        _prices = yaml.safe_load(PRICES_FILE.read_text(encoding="utf-8"))
    return _prices


def usage_of(messages):
    """One row per model response: which model, and its tokens. The fallback chain
    may switch models mid-run, so cost is counted per response, not per run."""
    return [{"model": f"{m.provider_name}:{m.model_name}",
             "input_tokens": m.usage.input_tokens or 0,
             "output_tokens": m.usage.output_tokens or 0}
            for m in messages if isinstance(m, ModelResponse) and m.model_name]


def cost_usd(rows):
    """Dollars for usage rows, or None if a model has no price (unknown is not $0)."""
    total = 0.0
    for r in rows:
        p = prices().get(r["model"])
        if p is None:
            return None
        total += r["input_tokens"] / 1e6 * p["input"] + r["output_tokens"] / 1e6 * p["output"]
    return total


def totals(rows):
    return {"input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "cost_usd": cost_usd(rows)}
