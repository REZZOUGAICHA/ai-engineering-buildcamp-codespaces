from pydantic_ai.messages import ToolCallPart


def collect_tools(result) -> list[ToolCallPart]:
    """Return the tool calls the agent made in this run, in order.

    PydanticAI returns structured output through an extra tool called
    `final_result`; it is left out because the agent didn't choose it.
    """
    tool_calls = []

    for message in result.new_messages():
        for part in message.parts:
            if part.part_kind == "tool-call" and part.tool_name != "final_result":
                tool_calls.append(part)

    return tool_calls
