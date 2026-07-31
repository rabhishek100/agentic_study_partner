"""Parser identity, importable without the layout-model toolchain.

``parsing.parser`` pulls in Unstructured and Torch, which cost roughly half a
gigabyte of resident memory. The worker records and compares this version on
every job but only needs the parser itself while a document is being parsed,
so the constant lives here and the heavy import stays deferred.
"""

# v2 restricts Tesseract to regions the PDF text layer does not cover.
# v3 consumes the exact preflight-approved normalized outline instead of
# re-reading raw publisher metadata, so its canonical hierarchy may differ.
# v4 resolves same-page outline boundaries from ordered extracted headings and
# marks repeated margin text as derived retrieval boilerplate.
# v5 isolates pages with pathological vector-path counts from pdfminer,
# preserving their native text and a rendered page visual instead.
PARSER_VERSION = "toc-hi-res-v5"
