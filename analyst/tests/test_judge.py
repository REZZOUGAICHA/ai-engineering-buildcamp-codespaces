"""LLM judge tests with the REAL model. Skipped by default; run with: uv run pytest -m llm"""

import os
from pathlib import Path

import anyio
import dotenv
import pytest

from analyst.answer import ask
from analyst.executor import Executor

dotenv.load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from judge import assert_criteria  # noqa: E402  (needs the keys loaded first)

pytestmark = [
    pytest.mark.llm,
    pytest.mark.anyio,
    pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="needs GROQ_API_KEY in .env"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_revenue_drop_names_the_planted_cause_without_claiming_it():
    # Planted story 1 (data/PLANTED.md): March 2026 revenue drop, concentrated
    # in EU and SMB, the same month an EU price increase was announced.
    question, role = "Why did revenue drop in March 2026?", "ceo"
    # ask() is synchronous, so it runs in a worker thread, away from the event loop.
    answer = await anyio.to_thread.run_sync(ask, question, role, Executor())

    assert answer.behavior == "answer"
    # Not "describe_metric was called": the model only sometimes needs it (seen 2026-11-05).
    assert answer.plan.tool_calls[0] == "list_metrics"
    assert [p.metric for p in answer.plan.plans] == ["revenue"]

    await assert_criteria(answer, [
        "the answer says revenue fell from February 2026 to March 2026, and the size of the "
        "drop it states matches change and change_pct in the driver report",
        "the answer names region EU and segment SMB as where the drop is concentrated, "
        "matching the top_group of those dimensions in the driver report",
        "the answer does not say the EU Basic price increase caused the drop; it only says "
        "the drop coincides with it",
    ], question, role)
