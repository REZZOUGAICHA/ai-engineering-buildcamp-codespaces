import logfire
from dotenv import load_dotenv
from pydantic_ai import Agent

from trivia_tools import TriviaTools

load_dotenv()  # must run first: it loads LOGFIRE_TOKEN from .env

# With a token, traces go to the Logfire dashboard. Without one, nothing is
# sent, and the spans are only printed in the terminal.
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()

instructions = """You are a trivia quizmaster. When asked to play trivia:
1. Use the available tools to fetch trivia questions
2. Ask the player one question at a time with multiple choice options
3. Wait for their answer before moving to the next question
4. When the player answers, explain why the correct answer is correct - add interesting context and facts
5. After all questions, give the final score
"""

trivia_tools = TriviaTools()

# The homework uses 'openai:gpt-4o-mini'. We have no OpenAI key, so we use a
# free Groq model. Qwen (groq:qwen/qwen3.8-27b) sometimes wrote its tool call
# as plain "<tool_call>" text instead of calling the tool (1 of 3 runs in
# compare_models.py); gpt-oss-120b called both tools in 3 of 3.
agent = Agent(
    'groq:openai/gpt-oss-120b',
    name='agent',
    tools=[trivia_tools.get_categories, trivia_tools.get_questions],
    instructions=instructions,
)
