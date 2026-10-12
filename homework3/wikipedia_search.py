import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_PAGE_URL = "https://en.wikipedia.org/w/index.php"

# Wikipedia blocks requests that don't identify themselves with a User-Agent.
HEADERS = {"User-Agent": "ai-engineering-buildcamp/0.1 (learning project)"}

# Retry failed requests up to 3 times, waiting 1s, 2s, 4s between tries,
# because the connection to Wikipedia sometimes times out.
retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=retry))


def search_wikipedia(query: str) -> dict:
    """Search Wikipedia and return the raw JSON response."""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
    }
    response = session.get(WIKIPEDIA_API_URL, params=params, headers=HEADERS, timeout=10)
    response.raise_for_status()
    return response.json()


def get_page(title: str) -> str:
    """Fetch a Wikipedia page by its exact title and return its raw wikitext."""
    params = {
        "title": title,
        "action": "raw",
    }
    response = session.get(WIKIPEDIA_PAGE_URL, params=params, headers=HEADERS, timeout=10)
    response.raise_for_status()
    return response.text


if __name__ == "__main__":
    data = search_wikipedia("capybara")
    results = data["query"]["search"]

    print(f"Results returned: {len(results)}")
    print(f"Total matches on Wikipedia: {data['query']['searchinfo']['totalhits']}")
    for r in results:
        print("-", r["title"])

    content = get_page("Capybara")
    print(f"Characters in 'Capybara' page: {len(content)}")
