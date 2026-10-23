"""Record the token usage of every agent run, to know what a test run costs.

Agent.run is replaced by a wrapper that calls the original and then stores the
run's usage per model. Both the SQL agent and the judge go through Agent.run,
so judge costs are counted too.
"""
from collections import defaultdict

from genai_prices import Usage, calc_price
from pydantic_ai import Agent

# model name -> [input tokens, output tokens, number of runs]
usage_by_model = defaultdict(lambda: [0, 0, 0])

_original_run = Agent.run


async def _run_with_tracking(self, *args, **kwargs):
    result = await _original_run(self, *args, **kwargs)

    usage = result.usage
    model_name = result.response.model_name or "unknown"
    totals = usage_by_model[model_name]
    totals[0] += usage.input_tokens
    totals[1] += usage.output_tokens
    totals[2] += 1

    return result


def patch_agent():
    Agent.run = _run_with_tracking


def list_price(model_name: str, input_tokens: int, output_tokens: int) -> float | None:
    """Price on the provider's paid tier, or None if the model isn't in genai-prices."""
    provider = {"qwen/qwen3.8-27b": "groq"}.get(model_name)
    try:
        price = calc_price(
            Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            model_ref=model_name,
            provider_id=provider,
        )
    except LookupError:
        return None
    return float(price.total_price)


def gpt_4o_mini_price(input_tokens: int, output_tokens: int) -> float:
    """What the same tokens would cost on gpt-4o-mini, the homework's model."""
    price = calc_price(
        Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        model_ref="gpt-4o-mini",
        provider_id="openai",
    )
    return float(price.total_price)


def cost_report() -> str:
    lines = ["", "===== cost report ====="]
    total_in = total_out = 0
    total_list = 0.0

    for model_name, (input_tokens, output_tokens, runs) in usage_by_model.items():
        total_in += input_tokens
        total_out += output_tokens
        price = list_price(model_name, input_tokens, output_tokens)
        if price is not None:
            total_list += price
        price_text = f"${price:.4f}" if price is not None else "no price data"
        lines.append(
            f"{model_name}: {runs} runs, {input_tokens:,} in / {output_tokens:,} out tokens,"
            f" paid-tier price {price_text}"
        )

    lines.append(f"total tokens: {total_in:,} in / {total_out:,} out")
    lines.append("actual cost: $0 (all models used on free tiers)")
    lines.append(f"paid-tier price (known models only): ${total_list:.4f}")
    lines.append(f"same tokens on gpt-4o-mini: ${gpt_4o_mini_price(total_in, total_out):.4f}")
    return "\n".join(lines)
