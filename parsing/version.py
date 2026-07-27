"""Parser identity, importable without the layout-model toolchain.

``parsing.parser`` pulls in Unstructured and Torch, which cost roughly half a
gigabyte of resident memory. The worker records and compares this version on
every job but only needs the parser itself while a document is being parsed,
so the constant lives here and the heavy import stays deferred.
"""

# v2 restricts Tesseract to regions the PDF text layer does not cover, so a
# book committed by v1 is not interchangeable with one committed by v2.
PARSER_VERSION = "toc-hi-res-v2"
