import tempfile

import fitz  # pymupdf
from langchain_community.document_loaders import UnstructuredPDFLoader

SOURCE_PDF = "sources/books/designing.machine.learning.systems.pdf"
NUM_PAGES = 10  # first chapter, first few pages


def extract_first_pages(source_path: str, num_pages: int) -> str:
    src = fitz.open(source_path)
    excerpt = fitz.open()
    excerpt.insert_pdf(src, from_page=0, to_page=min(num_pages, src.page_count) - 1)

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    excerpt.save(tmp.name)
    excerpt.close()
    src.close()
    return tmp.name


excerpt_path = extract_first_pages(SOURCE_PDF, NUM_PAGES)

loader = UnstructuredPDFLoader(
    excerpt_path,
    mode="elements",
    strategy="hi_res",  # layout-model based, detects titles/tables/text separately
)

docs = loader.load()

for i, doc in enumerate(docs):
    category = doc.metadata.get("category", "Unknown")
    page = doc.metadata.get("page_number", "?")
    print(f"[{i}] page={page} category={category}")
    print(doc.page_content)
    print("-" * 80)
