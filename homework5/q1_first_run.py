"""Q1: run the agent once and print every tool call, in order."""
from pydantic_ai.messages import ToolCallPart

from trivia_agent import agent

result = agent.run_sync("Let's play 3 medium questions from History")

print("Tool calls, in order:")
for message in result.all_messages():
    for part in message.parts:
        if isinstance(part, ToolCallPart):
            print(f"  {part.tool_name}({part.args})")

print("\nAgent says:\n", result.output)
