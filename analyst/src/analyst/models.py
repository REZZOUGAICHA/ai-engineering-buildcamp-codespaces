"""Which language model to use, in one place.

Groq keys first (same Qwen model on every key, so switching keys doesn't
change answers), then Z.ai's GLM as a different-provider backup. A model is
skipped when its key is missing. ANALYST_MODEL overrides everything (e.g.
"groq:qwen/qwen3.8-27b" to measure one model alone).

max_retries=0: the clients otherwise wait and retry a 429 by themselves,
sometimes for minutes, before the fallback gets a chance to switch.
"""

import os

from groq import AsyncGroq
from openai import AsyncOpenAI
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.models.zai import ZaiModel
from pydantic_ai.providers.groq import GroqProvider
from pydantic_ai.providers.zai import ZaiProvider

GROQ_MODEL = "qwen/qwen3.8-27b"
ZAI_MODEL = "glm-4.7-flash"
GROQ_KEYS = ["GROQ_API_KEY", "GROQ_API_KEY2", "GROQ_API_KEY3"]


def build():
    """A model (or model name) for pydantic-ai, from the keys present in the environment."""
    if os.environ.get("ANALYST_MODEL"):
        return os.environ["ANALYST_MODEL"]
    chain = []
    for name in GROQ_KEYS:
        if os.environ.get(name):
            client = AsyncGroq(api_key=os.environ[name], max_retries=0, timeout=120)
            chain.append(GroqModel(GROQ_MODEL, provider=GroqProvider(groq_client=client)))
    if os.environ.get("ZAI_API_KEY"):
        client = AsyncOpenAI(api_key=os.environ["ZAI_API_KEY"], max_retries=0, timeout=120,
                             base_url="https://api.z.ai/api/paas/v4/")
        chain.append(ZaiModel(ZAI_MODEL, provider=ZaiProvider(openai_client=client)))
    if not chain:
        raise RuntimeError("No model key found: add GROQ_API_KEY (or ZAI_API_KEY) to .env")
    return chain[0] if len(chain) == 1 else FallbackModel(*chain)


def describe():
    """A short label for reports: which models are configured, in order."""
    if os.environ.get("ANALYST_MODEL"):
        return os.environ["ANALYST_MODEL"]
    names = [f"groq:{GROQ_MODEL} ({k})" for k in GROQ_KEYS if os.environ.get(k)]
    if os.environ.get("ZAI_API_KEY"):
        names.append(f"zai:{ZAI_MODEL}")
    return " -> ".join(names)
