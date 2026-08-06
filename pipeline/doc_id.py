"""
doc_id.py — stable document identity
=====================================
Every indexed file gets a `doc_id` written into the Chroma metadata of each of
its chunks. That doc_id is the join key between ChromaDB (which holds the text)
and auth.db (which holds who may see it).

Why a content hash and not the filename:
  Two departments both upload "Q4_Report.docx" with different contents. Keyed on
  filename, one file's permissions silently overwrite the other's, and a Sales
  user could end up reading HR's document because the names collided. Keyed on
  content, they are two distinct doc_ids with independent permissions.

The flip side: re-uploading a file with one character changed produces a new
doc_id, so it arrives with no permissions and is invisible until tagged. That
is the correct failure direction — a new document defaults to nobody rather
than inheriting access it was never granted.

This reuses the same SHA1 scheme as pipeline/library.py so the library filename
and the doc_id stay derivable from one another.
"""

import hashlib

DIGEST_LENGTH = 16          # 16 hex chars ≈ 64 bits — collision-safe at this scale
LEGACY_PREFIX = "legacy:"   # see scripts/migrate_acl.py


def compute_doc_id(file_path: str) -> str:
    """SHA1 of the file's bytes, truncated. Streams the file so large PDFs don't
    get loaded into memory whole."""
    h = hashlib.sha1()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:DIGEST_LENGTH]


def compute_doc_id_from_bytes(data: bytes) -> str:
    """For content already in memory (e.g. a Streamlit upload buffer)."""
    return hashlib.sha1(data).hexdigest()[:DIGEST_LENGTH]


def legacy_doc_id(source: str) -> str:
    """
    Deterministic doc_id for chunks indexed before doc_id existed.

    The original file is long gone from those code paths, so its bytes can't be
    re-hashed. Deriving from the source filename is the only option available,
    and it's what scripts/migrate_acl.py backfills. Collisions are possible in
    principle; in practice the existing corpus has unique filenames.
    """
    return LEGACY_PREFIX + source


def chunk_doc_id(meta: dict) -> str:
    """
    Read the doc_id out of a chunk's Chroma metadata, falling back to the legacy
    form. Every ACL check in the retriever goes through this, so a chunk that
    somehow escaped the migration still resolves to *something* — and something
    unregistered is invisible to non-admins, which is the safe default.
    """
    return meta.get("doc_id") or legacy_doc_id(meta.get("source", "unknown"))
