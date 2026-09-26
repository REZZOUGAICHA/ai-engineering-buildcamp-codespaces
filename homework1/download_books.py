import csv
from pathlib import Path

import requests

books_dir = Path("books")
books_dir.mkdir(exist_ok=True)

with open("books.csv") as f:
    books = list(csv.DictReader(f))

for book in books:
    url = book["pdf_url"]
    path = books_dir / url.split("/")[-1]

    if path.exists():
        print(f"already have {path}")
        continue

    print(f"downloading {book['title']} -> {path}")
    response = requests.get(url)
    response.raise_for_status()
    path.write_bytes(response.content)

print(f"{len(books)} books in {books_dir}/")
