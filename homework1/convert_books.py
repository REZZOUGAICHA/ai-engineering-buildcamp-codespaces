from pathlib import Path

from markitdown import MarkItDown

books_dir = Path("books")
text_dir = Path("books_text")
text_dir.mkdir(exist_ok=True)

md = MarkItDown()

for pdf_path in sorted(books_dir.glob("*.pdf")):
    out_path = text_dir / (pdf_path.stem + ".md")

    if out_path.exists():
        print(f"already have {out_path}")
        continue

    print(f"converting {pdf_path} -> {out_path}")
    result = md.convert(str(pdf_path))
    out_path.write_text(result.text_content, encoding="utf-8")
