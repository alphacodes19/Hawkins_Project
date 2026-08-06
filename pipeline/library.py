"""
library.py — Permanent document storage
=========================================
Problem this solves:
  ZIP uploads get extracted to a temp folder, indexed, then deleted.
  Live uploads get written to tempfile, indexed, then os.unlink'd.
  In both cases the `file_path` saved in ChromaDB metadata pointed to a
  file that no longer existed seconds after indexing — so "open/download
  document" silently failed for anything except the original batch data.

Fix: every file that gets indexed is first copied into LIBRARY_DIR with a
collision-safe name. That permanent path is what gets stored as file_path.
"""

import os
import shutil
import hashlib
import config

LIBRARY_DIR = os.path.join(config.BASE_DIR, "data", "library")
os.makedirs(LIBRARY_DIR, exist_ok=True)


def store_in_library(src_path, original_name=None, origin_tag=""):
    """
    Copy src_path into the permanent library folder.
    Returns the permanent path to use as file_path metadata.

    Collision-safe: if a file with the same name already exists,
    a short content hash is appended so re-uploads of the same file
    map to the same library copy (dedup) instead of piling up.
    """
    if original_name is None:
        original_name = os.path.basename(src_path)

    base, ext = os.path.splitext(original_name)

    # Hash content so identical re-uploads reuse the same library file
    # instead of creating duplicates every time someone re-indexes.
    h = hashlib.sha1()
    with open(src_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    digest = h.hexdigest()[:10]

    safe_base = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)
    dest_name = f"{safe_base}__{digest}{ext}"
    dest_path = os.path.join(LIBRARY_DIR, dest_name)

    if not os.path.exists(dest_path):
        shutil.copyfile(src_path, dest_path)

    return dest_path
