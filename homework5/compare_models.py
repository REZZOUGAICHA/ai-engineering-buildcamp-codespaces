"""Run the first turn 3 times per model and check the tools are really called."""
import sys
import time

from pydantic_ai.messages import ToolCallPart

from trivia_agent import agent

PROMPT = "Let's play 3 medium questions from History"
models = sys.argv[1:]

for model in models:
    good = 0
    for i in range(3):
        # Open Trivia DB allows 1 request per 5 s per IP; faster calls get
        # HTTP 429 with {'result': []}, and the tool fails with KeyError.
        time.sleep(6)
        try:
            result = agent.run_sync(PROMPT, model=model)
        except Exception as e:
            print(f"{model} run {i + 1}: ERROR {type(e).__name__}: {str(e)[:120]}")
            continue
        tools = [p.tool_name for m in result.all_messages()
                 for p in m.parts if isinstance(p, ToolCallPart)]
        fake = "<tool_call>" in result.output
        ok = "get_questions" in tools and not fake
        good += ok
        print(f"{model} run {i + 1}: tools={tools} fake_tool_text={fake} ok={ok}")
    print(f"==> {model}: {good}/3 good\n")
