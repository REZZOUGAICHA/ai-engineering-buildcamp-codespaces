"""An LLM judge for the analyst's final answer (the course's assert_criteria pattern).

Code already checks numbers, placeholders and cause words (verifier.py). The
judge checks things that are hard to write as code: does the text name the
right groups, does it stay at "coincides with" instead of claiming a cause.
"""

from pydantic import BaseModel
from pydantic_ai import Agent

from analyst import models, planner


class CriterionResult(BaseModel):
    criterion: str
    passed: bool
    reasoning: str


class JudgeFeedback(BaseModel):
    criteria: list[CriterionResult]
    feedback: str


JUDGE_INSTRUCTIONS = """
You are a strict judge of an AI data analyst. A manager asked a business
question; the analyst planned a query, code computed the results and a
"driver report" (which groups the change came from), and the analyst wrote
the final answer.

The tool calls, plan, results and driver report are the ground truth. Check
the final answer against each criterion separately. For each one, decide
passed = true or false and explain why in 1-2 sentences, quoting the answer
or the ground truth. A criterion passes only if the evidence clearly supports
it. Return one result per criterion, in the same order as given.
""".strip()

# Limitation: the judge uses the same model chain as the agent. A model judging
# its own family's work can be too lenient; a stronger, different judge is better.
judge_agent = Agent(name="judge", model=models.build(), instructions=JUDGE_INSTRUCTIONS,
                    output_type=JudgeFeedback, model_settings=planner.SETTINGS)


def judge_prompt(criteria, answer, question, role):
    results = "\n".join(f"- {rid}: {r.model_dump_json(include={'plan', 'rows'})}"
                        for rid, r in answer.results.items())
    drivers = answer.drivers.model_dump_json() if answer.drivers else "none"
    criteria_text = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
    return f"""
<QUESTION role="{role}">
{question}
</QUESTION>

<TOOL_CALLS>
{answer.plan.tool_calls}
</TOOL_CALLS>

<PLAN>
{[p.model_dump(mode="json") for p in answer.plan.plans]}
</PLAN>

<RESULTS>
{results}
</RESULTS>

<DRIVER_REPORT>
{drivers}
</DRIVER_REPORT>

<FINAL_ANSWER>
{answer.text}
</FINAL_ANSWER>

<CRITERIA>
{criteria_text}
</CRITERIA>
""".strip()


async def assert_criteria(answer, criteria, question, role) -> JudgeFeedback:
    run = await judge_agent.run(judge_prompt(criteria, answer, question, role))
    feedback = run.output

    assert len(feedback.criteria) == len(criteria), (
        f"judge returned {len(feedback.criteria)} results for {len(criteria)} criteria")
    for c in feedback.criteria:
        assert c.passed, f"Criterion failed: {c.criterion}\nJudge reasoning: {c.reasoning}"
    return feedback
