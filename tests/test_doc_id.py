"""
tests/test_doc_id.py
====================
Unit tests for pipeline/doc_id.py:
  - compute_doc_id_from_bytes() is stable (same content → same ID)
  - compute_doc_id_from_bytes() is sensitive to content (different content → different ID)
  - compute_doc_id() hashes a real file on disk
  - legacy_doc_id() produces the correct prefix format
  - chunk_doc_id() reads doc_id from metadata, falls back to legacy form
  - DIGEST_LENGTH is enforced
  - Empty-bytes edge case
"""

import os
import pytest
from pipeline.doc_id import (
    compute_doc_id,
    compute_doc_id_from_bytes,
    legacy_doc_id,
    chunk_doc_id,
    DIGEST_LENGTH,
    LEGACY_PREFIX,
)


# ── compute_doc_id_from_bytes ─────────────────────────────────────────────────

class TestComputeDocIdFromBytes:
    def test_returns_hex_string(self):
        doc_id = compute_doc_id_from_bytes(b"hawkins cookers")
        assert isinstance(doc_id, str)
        int(doc_id, 16)  # must be valid hex

    def test_digest_length_enforced(self):
        doc_id = compute_doc_id_from_bytes(b"any content here")
        assert len(doc_id) == DIGEST_LENGTH

    def test_stable_across_calls(self):
        data = b"Q4 Financial Report 2025"
        assert compute_doc_id_from_bytes(data) == compute_doc_id_from_bytes(data)

    def test_different_content_different_id(self):
        id1 = compute_doc_id_from_bytes(b"document A content")
        id2 = compute_doc_id_from_bytes(b"document B content")
        assert id1 != id2

    def test_single_byte_difference_changes_id(self):
        base = b"Hawkins Cookers Annual Report 2025"
        modified = base[:-1] + b"4"   # 2025 → 2024
        assert compute_doc_id_from_bytes(base) != compute_doc_id_from_bytes(modified)

    def test_empty_bytes_does_not_raise(self):
        doc_id = compute_doc_id_from_bytes(b"")
        assert len(doc_id) == DIGEST_LENGTH

    def test_large_content(self):
        large = b"x" * 10_000_000  # 10MB
        doc_id = compute_doc_id_from_bytes(large)
        assert len(doc_id) == DIGEST_LENGTH

    def test_binary_content(self):
        binary = bytes(range(256)) * 100
        doc_id = compute_doc_id_from_bytes(binary)
        assert len(doc_id) == DIGEST_LENGTH


# ── compute_doc_id (file path) ────────────────────────────────────────────────

class TestComputeDocId:
    def test_file_id_matches_bytes_id(self, tmp_path):
        content = b"Hawkins Cookers Limited - Product Manual v2.3"
        f = tmp_path / "manual.pdf"
        f.write_bytes(content)
        assert compute_doc_id(str(f)) == compute_doc_id_from_bytes(content)

    def test_stable_across_reads(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_bytes(b"stable content")
        id1 = compute_doc_id(str(f))
        id2 = compute_doc_id(str(f))
        assert id1 == id2

    def test_changed_file_changes_id(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"version one")
        id1 = compute_doc_id(str(f))
        f.write_bytes(b"version two")
        id2 = compute_doc_id(str(f))
        assert id1 != id2

    def test_same_content_different_filenames_same_id(self, tmp_path):
        content = b"identical content"
        f1 = tmp_path / "report_sales.pdf"
        f2 = tmp_path / "report_hr.pdf"
        f1.write_bytes(content)
        f2.write_bytes(content)
        # Content identity: same bytes → same doc_id regardless of filename
        assert compute_doc_id(str(f1)) == compute_doc_id(str(f2))

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            compute_doc_id(str(tmp_path / "nonexistent.pdf"))


# ── legacy_doc_id ─────────────────────────────────────────────────────────────

class TestLegacyDocId:
    def test_has_correct_prefix(self):
        lid = legacy_doc_id("Q4_Report.docx")
        assert lid.startswith(LEGACY_PREFIX)

    def test_source_preserved_after_prefix(self):
        source = "Vendor_Contract_Presstek.pdf"
        lid = legacy_doc_id(source)
        assert source in lid

    def test_stable(self):
        assert legacy_doc_id("same.pdf") == legacy_doc_id("same.pdf")

    def test_different_sources_different_ids(self):
        assert legacy_doc_id("file_a.pdf") != legacy_doc_id("file_b.pdf")


# ── chunk_doc_id ──────────────────────────────────────────────────────────────

class TestChunkDocId:
    def test_returns_doc_id_when_present(self):
        meta = {"doc_id": "abcdef1234567890", "source": "report.pdf"}
        assert chunk_doc_id(meta) == "abcdef1234567890"

    def test_falls_back_to_legacy_when_doc_id_missing(self):
        meta = {"source": "old_file.pdf"}  # no doc_id key
        result = chunk_doc_id(meta)
        assert result == legacy_doc_id("old_file.pdf")

    def test_falls_back_when_doc_id_is_empty_string(self):
        meta = {"doc_id": "", "source": "file.pdf"}
        result = chunk_doc_id(meta)
        assert result == legacy_doc_id("file.pdf")

    def test_falls_back_when_doc_id_is_none(self):
        meta = {"doc_id": None, "source": "file.pdf"}
        result = chunk_doc_id(meta)
        assert result == legacy_doc_id("file.pdf")

    def test_empty_meta_does_not_raise(self):
        result = chunk_doc_id({})
        # No doc_id, no source → falls back to legacy_doc_id("unknown")
        assert result == legacy_doc_id("unknown")

    def test_none_meta_raises_attribute_error(self):
        # chunk_doc_id expects a dict; passing None raises AttributeError.
        # This documents the current contract — callers must guard against None.
        # The retriever always passes (meta or {}) so this is acceptable.
        with pytest.raises(AttributeError):
            chunk_doc_id(None)
