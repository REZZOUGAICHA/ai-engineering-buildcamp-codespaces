"""Q6: total tokens and cost of the grouped session, summed over ALL agent spans."""
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from logfire.query_client import LogfireQueryClient

TRACE_ID = sys.argv[1] if len(sys.argv) > 1 else '01a1ac678e873347ff7e6b7806bdc0ff'

MODEL_PRICES = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},  # $ per 1M tokens
}


def calculate_cost(model_name, input_tokens, output_tokens):
    prices = MODEL_PRICES[model_name.lower()]
    input_cost = (input_tokens / 1_000_000) * prices["input"]
    output_cost = (output_tokens / 1_000_000) * prices["output"]
    return input_cost + output_cost


load_dotenv()


def main():
    client = LogfireQueryClient(read_token=os.environ['LOGFIRE_READ_TOKEN'])

    rows = client.query_json_rows(sql=f"""
        SELECT
            start_timestamp,
            -- newer pydantic-ai: gen_ai.aggregated_usage.* (sum over the run's
            -- LLM calls); older versions (the lesson): gen_ai.usage.*
            COALESCE(attributes->>'gen_ai.aggregated_usage.input_tokens',
                     attributes->>'gen_ai.usage.input_tokens') AS input_tokens,
            COALESCE(attributes->>'gen_ai.aggregated_usage.output_tokens',
                     attributes->>'gen_ai.usage.output_tokens') AS output_tokens
        FROM records
        WHERE trace_id = '{TRACE_ID}'
          AND (span_name = 'agent run' OR span_name LIKE 'invoke_agent %')
        ORDER BY start_timestamp ASC
    """, min_timestamp=datetime.now(timezone.utc) - timedelta(days=7))['rows']

    total_in = total_out = 0
    for i, r in enumerate(rows, 1):
        tin, tout = int(r['input_tokens']), int(r['output_tokens'])
        total_in += tin
        total_out += tout
        print(f"agent span {i}: input={tin:>5}  output={tout:>5}")

    cost = calculate_cost("gpt-4o-mini", total_in, total_out)
    print(f"\nagent spans: {len(rows)}")
    print(f"total input tokens:  {total_in}")
    print(f"total output tokens: {total_out}")
    print(f"cost at gpt-4o-mini prices: ${cost:.6f}")


if __name__ == "__main__":
    main()
