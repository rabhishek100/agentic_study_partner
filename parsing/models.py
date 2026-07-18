"""In-memory models produced by the PDF parser."""

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    text: str
    category: str
    page: int


class TableBlock(BaseModel):
    html: str | None = None
    text: str
    page: int


class ImageBlock(BaseModel):
    base64: str
    mime: str
    page: int


class Section(BaseModel):
    """The content directly owned by one table-of-contents entry."""

    path: list[str]
    level: int
    start_page: int
    end_page: int
    texts: list[TextBlock] = Field(default_factory=list)
    tables: list[TableBlock] = Field(default_factory=list)
    images: list[ImageBlock] = Field(default_factory=list)

    @property
    def chapter(self) -> str:
        return self.path[0]

    @property
    def title(self) -> str:
        return self.path[-1]

    @property
    def label(self) -> str:
        return " :: ".join(self.path)

    @property
    def full_text(self) -> str:
        return "\n\n".join(block.text for block in self.texts)


class ParsedBook(BaseModel):
    """Lossless parser output before it is persisted."""

    source: str
    toc: list[tuple[int, str, int]]
    sections: list[Section]

    def by_chapter(self, chapter: str) -> list[Section]:
        return [section for section in self.sections if section.chapter == chapter]
