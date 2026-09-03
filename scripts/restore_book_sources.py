"""Restore book-source PDFs into Supabase Storage from a recovery archive.

Unpacking the archive straight into the storage volume is not enough, and
the failure is quiet: the bytes land, the rows can be inserted by hand, and
every object then 500s on read with ENODATA. Supabase Storage keeps each
object's content type and cache headers in extended attributes on the file,
and a tar written without them restores a file the service cannot describe.

Uploading through the Storage API instead makes the service write both the
bytes and the metadata it will later look for, and create its own
storage.objects row to match. Slower, and the only version that works.

Usage:

    python -m scripts.restore_book_sources <extracted-archive-root>

where the root is the directory the storage tar unpacks into, the one
containing "stub/stub/book-sources/...".
"""
import os, pathlib, sys
import httpx
from dotenv import load_dotenv

load_dotenv("/Users/abhishek/Desktop/Projects/agentic_study_partner/.env")
BASE = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ROOT = pathlib.Path(sys.argv[1]) / "stub" / "stub"
BUCKET = "book-sources"

files = sorted(p for p in ROOT.rglob("*") if p.is_file())
print(f"{len(files)} files to upload")
ok = fail = 0
with httpx.Client(timeout=180) as client:
    for path in files:
        rel = path.relative_to(ROOT)          # book-sources/<owner>/<book>/original.pdf/<version>
        parts = rel.parts
        if parts[0] != BUCKET:
            print("  skip (unexpected bucket):", rel); continue
        name = "/".join(parts[1:-1])          # <owner>/<book>/original.pdf
        data = path.read_bytes()
        r = client.post(
            f"{BASE}/storage/v1/object/{BUCKET}/{name}",
            content=data,
            headers={
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/pdf",
                "cache-control": "no-cache",
                "x-upsert": "true",
            },
        )
        if r.status_code in (200, 201):
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {r.status_code} {name}: {r.text[:120]}")
print(f"uploaded ok={ok} failed={fail}")
