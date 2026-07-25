"""Parser identity, importable without the layout-model toolchain.

``parsing.parser`` pulls in Unstructured and Torch, which cost roughly half a
gigabyte of resident memory. The worker records and compares this version on
every job but only needs the parser itself while a document is being parsed,
so the constant lives here and the heavy import stays deferred.
"""

PARSER_VERSION = "toc-hi-res-v1"
