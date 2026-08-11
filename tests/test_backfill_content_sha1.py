"""
test_backfill_content_sha1.py — verify the legacy-hash backfill
================================================================
- recoverable files (present in the library dir) get content_sha1 set
- unrecoverable files stay NULL
- doc_id is never modified
- the script is idempotent (running twice = same state)
- --dry-run writes nothing
"""

import hashlib
import os
import subprocess
import sys

import pytest


def _write(path: str, content: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


def _sha1_16(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


@pytest.fixture
def project_env(tmp_path, monkeypatch):
    """
    Isolated auth.db + data/library/ under tmp_path.

    Chroma is mocked at the conftest level, so _load_chroma_file_paths in
    the script returns {} — the backfill then falls back to the
    data/library/ scan. That is the same fallback path a real backfill
    takes when a legacy file's Chroma-recorded file_path no longer resolves
    but its library copy is still present.
    """
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHROMA_PATH", str(tmp_path / "chroma"))

    import auth.db as authdb
    monkeypatch.setattr(authdb, "DB_PATH", str(tmp_path / "auth.db"))
    authdb.init_db()

    # Force pipeline.library.LIBRARY_DIR to point at the tmp path.
    import pipeline.library as libmod
    lib_dir = str(tmp_path / "data" / "library")
    os.makedirs(lib_dir, exist_ok=True)
    monkeypatch.setattr(libmod, "LIBRARY_DIR", lib_dir)

    return {"authdb": authdb, "lib_dir": lib_dir, "tmp": tmp_path}


def test_backfill_recovers_files_present_in_library(project_env, monkeypatch):
    authdb = project_env["authdb"]
    lib_dir = project_env["lib_dir"]

    # Two legacy rows. Only one has a corresponding file on disk.
    authdb.register_file(doc_id="legacy:present.pdf", source="present.pdf",
                         is_public=True, content_sha1=None)
    authdb.register_file(doc_id="legacy:missing.pdf", source="missing.pdf",
                         is_public=True, content_sha1=None)

    payload = b"real bytes for present.pdf" * 100
    expected = _sha1_16(payload)
    # data/library/ uses a "safe_base__10hex.ext" naming; the script
    # locates by safe_base + ext.
    _write(os.path.join(lib_dir, "present__abcdef0123.pdf"), payload)

    # Run the script main() directly.
    from scripts import backfill_content_sha1 as script
    monkeypatch.setattr("sys.argv", ["backfill_content_sha1.py", "--yes"])
    script.main()

    rows = {r["source"]: r for r in authdb.list_files()}
    assert rows["present.pdf"]["content_sha1"] == expected
    assert rows["missing.pdf"]["content_sha1"] is None

    # doc_id preserved.
    assert rows["present.pdf"]["doc_id"] == "legacy:present.pdf"
    assert rows["missing.pdf"]["doc_id"] == "legacy:missing.pdf"


def test_backfill_is_idempotent(project_env, monkeypatch):
    authdb = project_env["authdb"]
    lib_dir = project_env["lib_dir"]

    authdb.register_file(doc_id="legacy:x.pdf", source="x.pdf",
                         is_public=True, content_sha1=None)
    payload = b"identical" * 1000
    expected = _sha1_16(payload)
    _write(os.path.join(lib_dir, "x__1234567890.pdf"), payload)

    from scripts import backfill_content_sha1 as script
    monkeypatch.setattr("sys.argv", ["backfill_content_sha1.py", "--yes"])
    script.main()
    first = authdb.list_files()

    script.main()  # run again
    second = authdb.list_files()

    assert first == second
    assert first[0]["content_sha1"] == expected


def test_backfill_dry_run_writes_nothing(project_env, monkeypatch, capsys):
    authdb = project_env["authdb"]
    lib_dir = project_env["lib_dir"]

    authdb.register_file(doc_id="legacy:x.pdf", source="x.pdf",
                         is_public=True, content_sha1=None)
    _write(os.path.join(lib_dir, "x__1234567890.pdf"), b"anything")

    from scripts import backfill_content_sha1 as script
    monkeypatch.setattr("sys.argv", ["backfill_content_sha1.py", "--dry-run"])
    script.main()

    row = authdb.list_files()[0]
    assert row["content_sha1"] is None
    assert row["doc_id"] == "legacy:x.pdf"

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "Database not modified" in out


# ── Chroma pagination + $in batching ──────────────────────────────────────
class _FakeChromaCollection:
    """Minimal Chroma-like collection.

    Verifies two things:
    1. The script does NOT call get() with an unbounded, unfiltered payload
       that would trigger 'too many SQL variables' on the real DB.
       We refuse it here (raise) if it happens.
    2. Batched $in queries return the right per-source metadata.
    """

    def __init__(self, source_to_path, in_batch_limit=999):
        # {source: [chunk_metadata dicts...]}
        self.chunks = {}
        for src, path in source_to_path.items():
            self.chunks[src] = [{"source": src, "filepath": path,
                                 "doc_id": f"legacy:{src}"}]
        self.in_batch_limit = in_batch_limit
        self.calls = []  # log of every get() invocation

    def count(self):
        return sum(len(v) for v in self.chunks.values())

    def get(self, ids=None, where=None, limit=None, offset=None, include=None):
        self.calls.append({"where": where, "limit": limit, "offset": offset})

        # Refuse the "load everything without pagination" pattern that broke
        # the previous script.
        if where is None and limit is None:
            raise RuntimeError(
                "error returned from database: (code: 1) too many SQL variables"
            )

        # Sample query (limit=1) → return one chunk.
        if where is None and limit is not None and offset is None:
            all_metas = [m for metas in self.chunks.values() for m in metas]
            return {"metadatas": all_metas[:limit]}

        # Paginated scan.
        if where is None and limit is not None:
            all_metas = [m for metas in self.chunks.values() for m in metas]
            start = offset or 0
            return {"metadatas": all_metas[start:start + limit]}

        # $in filter.
        if where and "source" in where and "$in" in where["source"]:
            batch = where["source"]["$in"]
            if len(batch) > self.in_batch_limit:
                raise RuntimeError(
                    f"too many SQL variables ({len(batch)} > {self.in_batch_limit})"
                )
            metas = []
            for src in batch:
                metas.extend(self.chunks.get(src, []))
            return {"metadatas": metas}

        return {"metadatas": []}


def _install_fake_chroma(monkeypatch, collection):
    """Route the script's Chroma import to return our fake collection."""
    fake_module = MagicMock()

    class FakeClient:
        def __init__(self, path=None):
            self.path = path

        def get_collection(self, name):
            return collection

    fake_module.PersistentClient = FakeClient
    monkeypatch.setitem(sys.modules, "chromadb", fake_module)


from unittest.mock import MagicMock  # noqa: E402 — needed by helpers above


def test_backfill_recovers_via_chroma_filepath(project_env, monkeypatch, tmp_path):
    """
    Legacy rows recover from Chroma's `filepath` metadata even when the file
    is NOT in data/library/ — this is the real production scenario for
    pre-ACL corpus files.
    """
    authdb = project_env["authdb"]

    # Two legacy rows, files at real-sourced paths (not in data/library/).
    real_dir = tmp_path / "data" / "real_sourced" / "pdfs"
    real_dir.mkdir(parents=True)
    fp1 = str(real_dir / "UPSC Wallah Books  Disaster Management.pdf")
    fp2 = str(real_dir / "Another Doc.pdf")
    payload1 = b"pretend this is a real UPSC PDF" * 100
    payload2 = b"another doc's bytes" * 200
    with open(fp1, "wb") as f:
        f.write(payload1)
    with open(fp2, "wb") as f:
        f.write(payload2)

    authdb.register_file(doc_id="legacy:UPSC Wallah Books  Disaster Management.pdf",
                         source="UPSC Wallah Books  Disaster Management.pdf",
                         is_public=True, content_sha1=None)
    authdb.register_file(doc_id="legacy:Another Doc.pdf", source="Another Doc.pdf",
                         is_public=True, content_sha1=None)

    fake_col = _FakeChromaCollection({
        "UPSC Wallah Books  Disaster Management.pdf": fp1,
        "Another Doc.pdf": fp2,
    })
    _install_fake_chroma(monkeypatch, fake_col)

    from scripts import backfill_content_sha1 as script
    monkeypatch.setattr("sys.argv", ["backfill_content_sha1.py", "--yes"])
    script.main()

    rows = {r["source"]: r for r in authdb.list_files()}
    expected1 = hashlib.sha1(payload1).hexdigest()[:16]
    expected2 = hashlib.sha1(payload2).hexdigest()[:16]
    assert rows["UPSC Wallah Books  Disaster Management.pdf"]["content_sha1"] == expected1
    assert rows["Another Doc.pdf"]["content_sha1"] == expected2

    # doc_id must be untouched.
    assert rows["UPSC Wallah Books  Disaster Management.pdf"]["doc_id"] == \
        "legacy:UPSC Wallah Books  Disaster Management.pdf"


def test_backfill_batches_chroma_queries_under_sql_variable_limit(
    project_env, monkeypatch, tmp_path
):
    """
    Simulate the 'too many SQL variables' scenario: a fake Chroma that
    refuses any single query with more than 100 items in its $in clause.
    The script's batch size is 50, so this must still succeed.
    """
    authdb = project_env["authdb"]

    real_dir = tmp_path / "real"
    real_dir.mkdir()

    source_to_path = {}
    for i in range(200):  # 200 legacy rows → 4 batches at size 50
        src = f"doc_{i:03d}.pdf"
        fp = str(real_dir / src)
        with open(fp, "wb") as f:
            f.write(f"content-{i}".encode())
        source_to_path[src] = fp
        authdb.register_file(doc_id=f"legacy:{src}", source=src,
                             is_public=True, content_sha1=None)

    fake_col = _FakeChromaCollection(source_to_path, in_batch_limit=100)
    _install_fake_chroma(monkeypatch, fake_col)

    from scripts import backfill_content_sha1 as script
    monkeypatch.setattr("sys.argv", ["backfill_content_sha1.py", "--yes"])
    script.main()

    rows = {r["source"]: r for r in authdb.list_files()}
    for i in range(200):
        src = f"doc_{i:03d}.pdf"
        expected = hashlib.sha1(f"content-{i}".encode()).hexdigest()[:16]
        assert rows[src]["content_sha1"] == expected, f"row {src} not backfilled"

    # Every $in call must have been within the batch limit.
    for call in fake_col.calls:
        where = call["where"]
        if where and "source" in where and "$in" in where["source"]:
            assert len(where["source"]["$in"]) <= 50, \
                f"batch too large: {len(where['source']['$in'])}"


def test_backfill_dry_run_report_includes_focus_file(
    project_env, monkeypatch, tmp_path, capsys
):
    """The dry-run report must trace the UPSC focus file specifically."""
    authdb = project_env["authdb"]

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    fp = str(real_dir / "UPSC Wallah Books  Disaster Management.pdf")
    with open(fp, "wb") as f:
        f.write(b"upsc bytes" * 100)

    authdb.register_file(doc_id="legacy:UPSC Wallah Books  Disaster Management.pdf",
                         source="UPSC Wallah Books  Disaster Management.pdf",
                         is_public=True, content_sha1=None)

    fake_col = _FakeChromaCollection(
        {"UPSC Wallah Books  Disaster Management.pdf": fp}
    )
    _install_fake_chroma(monkeypatch, fake_col)

    from scripts import backfill_content_sha1 as script
    monkeypatch.setattr("sys.argv", ["backfill_content_sha1.py", "--dry-run"])
    script.main()

    out = capsys.readouterr().out
    assert "Focus file trace" in out
    assert "UPSC Wallah Books  Disaster Management.pdf" in out
    assert "Chroma match found          : yes" in out
    assert "SHA-1 if recovered" in out
    # Confirm dry-run left the row untouched.
    row = authdb.list_files()[0]
    assert row["content_sha1"] is None


def test_backfill_never_modifies_doc_id(project_env, monkeypatch, tmp_path):
    """Regression guard: doc_id must be exactly preserved through backfill."""
    authdb = project_env["authdb"]

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    fp = str(real_dir / "X.pdf")
    with open(fp, "wb") as f:
        f.write(b"bytes")

    original_doc_id = "legacy:X.pdf"
    authdb.register_file(doc_id=original_doc_id, source="X.pdf",
                         is_public=True, content_sha1=None)

    fake_col = _FakeChromaCollection({"X.pdf": fp})
    _install_fake_chroma(monkeypatch, fake_col)

    from scripts import backfill_content_sha1 as script
    monkeypatch.setattr("sys.argv", ["backfill_content_sha1.py", "--yes"])
    script.main()

    row = authdb.list_files()[0]
    assert row["doc_id"] == original_doc_id
    assert row["content_sha1"] is not None
