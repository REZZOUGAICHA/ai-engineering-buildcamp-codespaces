import os
import sys

import dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.zai import ZaiModel
from pydantic_ai.providers.zai import ZaiProvider

from sql_tools import SQLTools, setup_database

dotenv.load_dotenv()


class SQLResult(BaseModel):
    sql_query: str
    result_text: str
    row_count: int


INSTRUCTIONS = """
You answer questions about NYC yellow taxi trips from January 2024,
stored in a DuckDB table called `trips`.

Always start by calling get_schema to see the columns and their types,
before running any query. Then write a DuckDB SQL query and run it with run_sql.
If a query fails, read the error, fix the query and run it again.

Return the final SQL query you ran, the result as text, and the number of
rows the query returned. Copy every number in the result text exactly from
the run_sql output; never write a number that run_sql did not return.
""".strip()

# The homework uses 'openai:gpt-4o-mini'. We have no OpenAI key, so we use
# free models: Qwen on Groq first (fast; this agent's requests are small,
# well under Groq's 8,000-token limit), then Z.ai's GLM models. If one is
# rate-limited or overloaded (429), the next is tried.
# Not gpt-oss-120b on Groq: it ran the right SQL but then returned an empty
# reply instead of the SQLResult, so every test failed.
#
# max_retries=0: by default the client silently waits and retries on a 429,
# sometimes for minutes, before FallbackModel gets a chance to switch models.
# That looked like a hang, so errors are passed straight to FallbackModel.
zai_client = AsyncOpenAI(
    api_key=os.environ["ZAI_API_KEY"],
    base_url="https://api.z.ai/api/paas/v4/",
    max_retries=0,
    timeout=120,
)
zai = ZaiProvider(openai_client=zai_client)

MODEL = FallbackModel(
    'groq:qwen/qwen3.8-27b',
    ZaiModel('glm-4.7-flash', provider=zai),
    ZaiModel('glm-4.5-flash', provider=zai),
)

sql_tools = SQLTools()

sql_agent = Agent(
    name='sql',
    model=MODEL,
    instructions=INSTRUCTIONS,
    output_type=SQLResult,
    # the free models sometimes return an invalid SQLResult; the default of
    # 1 retry made runs crash with "Exceeded maximum output retries"
    retries=3,
    tools=[sql_tools.get_schema, sql_tools.run_sql],
)


if __name__ == "__main__":
    # Windows terminals default to cp1252, which can't print every character
    sys.stdout.reconfigure(encoding='utf-8')

    setup_database()

    question = "What's the average trip distance for rides with 2 passengers?"
    result = sql_agent.run_sync(question)

    # show which tools the agent called, in order
    for message in result.all_messages():
        for part in message.parts:
            if part.part_kind == 'tool-call' and part.tool_name != 'final_result':
                print(f"TOOL CALL: {part.tool_name}({part.args})")

    print()
    print(result.output)
    print()
    print(result.usage)
