from pydantic import BaseModel
from pydantic_ai import Agent

from sql_agent import MODEL
from utils import collect_tools


class CriterionResult(BaseModel):
    criterion: str
    passed: bool
    reasoning: str


class JudgeFeedback(BaseModel):
    criteria: list[CriterionResult]
    feedback: str


JUDGE_INSTRUCTIONS = """
You are an expert judge evaluating the work of an AI agent that answers
questions about NYC taxi trips by writing SQL queries against a DuckDB table.

You get the user's question, the tool calls the agent made with what each
call returned, and its final output. Check the output against each criterion
separately. The tool results are the ground truth: numbers in the output must
match what run_sql returned. Judge the SQL by what it returned, not by guesses
about how the database works.

For each criterion, decide if it is met (passed = true) or not (passed = false),
and explain why in 1-2 sentences, pointing to the SQL query or the result.
Be strict: a criterion passes only if the evidence clearly supports it.
Return one result per criterion, in the same order as given.
""".strip()

# The judge uses the same free models as the agent. A model judging its own
# family's work can be too lenient, so a stronger, different judge is better
# when one is available.
judge_agent = Agent(
    name='judge',
    model=MODEL,
    instructions=JUDGE_INSTRUCTIONS,
    output_type=JudgeFeedback,
)


async def evaluate_agent_performance(
        criteria: list[str],
        result,
        user_question: str,
    ) -> JudgeFeedback:
    # The judge needs the tool results, not only the calls: without them it
    # can't check that the numbers in the output came from the database.
    returns = {
        part.tool_call_id: part.content
        for message in result.new_messages()
        for part in message.parts
        if part.part_kind == "tool-return"
    }
    tool_calls = "\n".join(
        f"- {call.tool_name}({call.args})\n  returned: {returns.get(call.tool_call_id)}"
        for call in collect_tools(result)
    )
    criteria_text = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))

    prompt = f"""
<QUESTION>
{user_question}
</QUESTION>

<TOOL_CALLS>
{tool_calls}
</TOOL_CALLS>

<AGENT_OUTPUT>
{result.output}
</AGENT_OUTPUT>

<CRITERIA>
{criteria_text}
</CRITERIA>
""".strip()

    judge_result = await judge_agent.run(prompt)
    return judge_result.output


async def assert_criteria(criteria: list[str], result, user_question: str) -> JudgeFeedback:
    feedback = await evaluate_agent_performance(criteria, result, user_question)

    assert len(feedback.criteria) == len(criteria), (
        f"judge returned {len(feedback.criteria)} results for {len(criteria)} criteria"
    )

    for criterion in feedback.criteria:
        assert criterion.passed, (
            f"Criterion failed: {criterion.criterion}\n"
            f"Judge reasoning: {criterion.reasoning}"
        )

    return feedback
