"""
test_cleanup_orphaned_files.py
================================
Verifies:
  - ghost rows (no live Chroma backing at all) are correctly identified
    and removed
  - superseded rows (dead legacy: twin of a file that's also live under
    a real hash) are correctly identified and removed
  - live rows (doc_id present in Chroma) are never touched
  - dry-run writes nothing
  - file_dept rows cascade-delete when their parent files row is removed
  - idempotent — a second run finds nothing left to clean
"""

import os
import sys
import pytest
from unittest.mock import MagicMock


class _FakeCollection:
    """Chroma-like collection supporting only the paginated get() this
    script uses. `doc_ids` is the full list of doc_id values 'live' in
    the fake collection (order doesn't matter, duplicates are fine and
    mirror multiple chunks per document)."""

    def __init__(self, doc_ids):
        self.metas = [{"doc_id": d} for d in doc_ids]

    def get(self, limit=None, offset=None, include=None, **kwargs):
        start = offset or 0
        return {"metadatas": self.metas[start:start + (limit or len(self.metas))]}


def _install_fake_chroma(monkeypatch, collection):
    import types
    fake_chromadb = types.ModuleType("chromadb")

    class FakeClient:
        def __init__(self, path=None):
            pass

        def get_collection(self, name):
            return collection

    fake_chromadb.PersistentClient = FakeClient
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)


@pytest.fixture
def db(tmp_db):
    return tmp_db


def test_ghost_row_removed(db, monkeypatch):
    """A row whose doc_id has no live Chroma chunks and no live twin is a ghost."""
    db.register_file(doc_id="legacy:removed_test_file.pdf", source="removed_test_file.pdf",
                     is_public=True, content_sha1=None)
    db.register_file(doc_id="realhash1", source="live_file.pdf",
                     is_public=True, content_sha1="realhash1")

    _install_fake_chroma(monkeypatch, _FakeCollection(["realhash1"]))

    from scripts import cleanup_orphaned_files as script
    monkeypatch.setattr("sys.argv", ["cleanup_orphaned_files.py", "--yes"])
    script.main()

    remaining = {f["source"] for f in db.list_files()}
    assert remaining == {"live_file.pdf"}


def test_superseded_row_removed_live_twin_kept(db, monkeypatch):
    """
    A legacy: row for a file that got re-migrated under a real hash during
    reindex is superseded — remove the dead legacy row, keep the live one.
    """
    db.register_file(doc_id="legacy:report.pdf", source="report.pdf",
                     is_public=True, content_sha1=None)
    db.register_file(doc_id="realhash_report", source="report.pdf",
                     is_public=True, content_sha1="realhash_report")

    _install_fake_chroma(monkeypatch, _FakeCollection(["realhash_report"]))

    from scripts import cleanup_orphaned_files as script
    monkeypatch.setattr("sys.argv", ["cleanup_orphaned_files.py", "--yes"])
    script.main()

    rows = db.list_files()
    assert len(rows) == 1
    assert rows[0]["doc_id"] == "realhash_report"


def test_live_rows_never_touched(db, monkeypatch):
    db.register_file(doc_id="realhash1", source="a.pdf", is_public=True, content_sha1="realhash1")
    db.register_file(doc_id="realhash2", source="b.pdf", is_public=True, content_sha1="realhash2")

    _install_fake_chroma(monkeypatch, _FakeCollection(["realhash1", "realhash2"]))

    from scripts import cleanup_orphaned_files as script
    monkeypatch.setattr("sys.argv", ["cleanup_orphaned_files.py", "--yes"])
    script.main()

    remaining = {f["doc_id"] for f in db.list_files()}
    assert remaining == {"realhash1", "realhash2"}


def test_dry_run_writes_nothing(db, monkeypatch, capsys):
    db.register_file(doc_id="legacy:ghost.pdf", source="ghost.pdf",
                     is_public=True, content_sha1=None)
    db.register_file(doc_id="realhash1", source="live.pdf",
                     is_public=True, content_sha1="realhash1")

    _install_fake_chroma(monkeypatch, _FakeCollection(["realhash1"]))

    from scripts import cleanup_orphaned_files as script
    monkeypatch.setattr("sys.argv", ["cleanup_orphaned_files.py", "--dry-run"])
    script.main()

    assert len(db.list_files()) == 2  # nothing removed
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "Database not modified" in out
    assert "ghost.pdf" in out


def test_file_dept_cascades_on_delete(db, monkeypatch):
    """Deleting an orphaned files row must also remove its file_dept rows."""
    depts = db.list_departments()
    hr = next(d for d in depts if d["name"] == "HR")

    db.register_file(doc_id="legacy:ghost.pdf", source="ghost.pdf",
                     is_public=False, dept_ids=[hr["id"]], content_sha1=None)
    db.register_file(doc_id="realhash1", source="live.pdf",
                     is_public=True, content_sha1="realhash1")

    _install_fake_chroma(monkeypatch, _FakeCollection(["realhash1"]))

    from scripts import cleanup_orphaned_files as script
    monkeypatch.setattr("sys.argv", ["cleanup_orphaned_files.py", "--yes"])
    script.main()

    conn = db.get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM file_dept").fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_idempotent_second_run_finds_nothing(db, monkeypatch, capsys):
    db.register_file(doc_id="legacy:ghost.pdf", source="ghost.pdf",
                     is_public=True, content_sha1=None)
    db.register_file(doc_id="realhash1", source="live.pdf",
                     is_public=True, content_sha1="realhash1")

    _install_fake_chroma(monkeypatch, _FakeCollection(["realhash1"]))

    from scripts import cleanup_orphaned_files as script
    monkeypatch.setattr("sys.argv", ["cleanup_orphaned_files.py", "--yes"])
    script.main()
    assert len(db.list_files()) == 1

    capsys.readouterr()
    script.main()
    out = capsys.readouterr().out
    assert "Nothing to clean up" in out
    assert len(db.list_files()) == 1


def test_chroma_unreachable_aborts_without_deleting(db, monkeypatch, capsys):
    """If Chroma can't be read, the script must refuse to delete anything —
    better to do nothing than to delete based on an incomplete picture."""
    db.register_file(doc_id="legacy:x.pdf", source="x.pdf",
                     is_public=True, content_sha1=None)

    import types
    fake_chromadb = types.ModuleType("chromadb")

    class BrokenClient:
        def __init__(self, path=None):
            raise RuntimeError("cannot open chroma")

    fake_chromadb.PersistentClient = BrokenClient
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)

    from scripts import cleanup_orphaned_files as script
    monkeypatch.setattr("sys.argv", ["cleanup_orphaned_files.py", "--yes"])
    with pytest.raises(SystemExit):
        script.main()

    assert len(db.list_files()) == 1  # untouched
