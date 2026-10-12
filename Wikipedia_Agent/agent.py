import math
import re

import requests
from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext

import wikipedia_search

# Reads GROQ_API_KEY from the .env file in this folder.
load_dotenv()

# Groq's free tier allows 8,000 tokens per minute, and the agent resends the
# whole conversation on every step, so we send only the most relevant chunks.
CHUNK_SIZE = 3000
CHUNK_OVERLAP = 300
TOP_CHUNKS = 2

INSTRUCTIONS = """
You answer questions using Wikipedia.
First call search_wikipedia to find relevant pages, then call get_page
to read the most relevant one. If the user gives a Wikipedia URL, take the
page title from the URL and call get_page directly, without searching.
Read each page only once. Base your answer only on what you read,
and say which pages you used.
"""


def remove_refs(text: str) -> str:
    """Remove <ref> citations, which take a lot of space and don't help answer questions."""
    text = re.sub(r"<ref[^>]*/>", "", text)
    return re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)


def make_chunks(text: str) -> list[str]:
    """Split text into overlapping chunks so no sentence is lost at a boundary."""
    step = CHUNK_SIZE - CHUNK_OVERLAP
    return [text[i : i + CHUNK_SIZE] for i in range(0, len(text), step)]


def rank_chunks(chunks: list[str], query: str) -> list[str]:
    """Sort chunks by relevance to the query, best first.

    Each query word counts more when it is rare across the chunks (IDF, as in
    BM25), so a word like "capybara" that appears in every chunk of the
    Capybara page barely matters, while "gestation" matters a lot.
    """
    lowered = [c.lower() for c in chunks]
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    # Crude stemming: "threats" -> "threat", which also matches "threatened".
    words = [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words]
    n = len(chunks)

    weights = {}
    for w in words:
        df = sum(1 for c in lowered if w in c)
        weights[w] = math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score(i: int) -> float:
        return sum(lowered[i].count(w) * weights[w] for w in words)

    order = sorted(range(n), key=score, reverse=True)
    return [chunks[i] for i in order]



def search_wikipedia(query: str) -> list[dict]:
    """Search Wikipedia and return the title and a short snippet for each result."""
    try:
        data = wikipedia_search.search_wikipedia(query)
    except requests.RequestException as e:
        # Tell the model instead of crashing the whole run.
        return [{"error": f"Could not reach Wikipedia: {type(e).__name__}"}]
    return [{"title": r["title"], "snippet": r["snippet"]} for r in data["query"]["search"]]


def already_fetched(ctx: RunContext, title: str) -> bool:
    """Check whether get_page was already called for this title earlier in this run."""
    for message in ctx.messages:
        for part in message.parts:
            if (
                part.part_kind == "tool-call"
                and part.tool_name == "get_page"
                and part.tool_call_id != ctx.tool_call_id
                and part.args_as_dict().get("title", "").lower() == title.lower()
            ):
                return True
    return False


def get_page(ctx: RunContext, title: str, query: str) -> str:
    """Fetch a Wikipedia page by its exact title and return its introduction plus
    the parts most relevant to the query.

    The query is matched word by word, so include several related keywords and
    synonyms, e.g. "threats hunting predators conservation" rather than "threats".
    """
    # Re-reading a page returns nearly the same chunks and grows the conversation
    # past Groq's token limit, so answer repeats with a short note instead.
    if already_fetched(ctx, title):
        return f"You already read '{title}' above. Answer using that content, or try a different page."

    try:
        content = remove_refs(wikipedia_search.get_page(title))
    except requests.RequestException as e:
        return f"Could not fetch the page from Wikipedia: {type(e).__name__}"
    chunks = make_chunks(content)
    # The first chunk is the page's introduction, which summarises the whole
    # article, so always keep it and fill the rest with the best-ranked chunks.
    best = [chunks[0]] + rank_chunks(chunks[1:], query)[: TOP_CHUNKS - 1]
    return "\n\n[...]\n\n".join(best)


agent = Agent(
    "groq:openai/gpt-oss-120b",
    instructions=INSTRUCTIONS,
    tools=[search_wikipedia, get_page],
)


if __name__ == "__main__":
    result = agent.run_sync("Where do capybaras live?")

    # Show which tools the agent called, so we can see its steps.
    for message in result.all_messages():
        for part in message.parts:
            if part.part_kind == "tool-call":
                print(f"[tool call] {part.tool_name}({part.args})")

    print()
    print(result.output)
    print()
    print(result.usage)
