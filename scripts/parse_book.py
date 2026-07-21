"""Parse the configured source PDF and print a TOC-aligned summary."""

from parsing.parser import parse_book


SOURCE_PDF = "sources/books/designing.machine.learning.systems.pdf"


def main() -> None:
    book = parse_book(SOURCE_PDF)

    for section in book.sections:
        indent = "  " * (section.level - 1)
        print(
            f"{indent}{section.title} "
            f"(pp. {section.start_page}-{section.end_page}) "
            f"| {len(section.full_text)} chars, "
            f"{len(section.tables)} tables, "
            f"{len(section.images)} images"
        )

    biggest = max(book.sections, key=lambda item: len(item.full_text))
    print(f"\nLargest unit: {biggest.label} — {len(biggest.full_text)} chars")


if __name__ == "__main__":
    main()
