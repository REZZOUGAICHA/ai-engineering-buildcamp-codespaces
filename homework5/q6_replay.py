"""Q6: rebuild the grouped session as an AgentRunResult and compute its cost."""
import os

from dotenv import load_dotenv
from logfire.query_client import LogfireQueryClient

import trace_replay
from q6_cost import calculate_cost

TRACE_ID = '01a1ac678e873347ff7e6b7806bdc0ff'  # grouped session from Q4/Q5

load_dotenv()
client = LogfireQueryClient(read_token=os.environ['LOGFIRE_READ_TOKEN'])

trace = trace_replay.fetch_trace(TRACE_ID, client)
run = trace_replay.trace_to_run_result(trace)

usage = run.usage  # a property in pydantic-ai 2.x, not a method
print(f"type: {type(run).__name__}")
print(f"messages: {len(run.all_messages())}")
print(f"input tokens: {usage.input_tokens}, output tokens: {usage.output_tokens}")
print(f"cost at gpt-4o-mini prices: ${calculate_cost('gpt-4o-mini', usage.input_tokens, usage.output_tokens):.6f}")
print(f"\nfinal output (first 300 chars):\n{str(run.output)[:300]}")
