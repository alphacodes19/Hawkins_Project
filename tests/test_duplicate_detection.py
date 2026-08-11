"""
test_duplicate_detection.py — correctness of the corrected exact-duplicate
============================================================================
Covers every case laid out in the plan:

  1. same filename + same content              → exact_duplicate
  2. different filename + same content         → exact_duplicate  ← the UPSC bug
  3. same filename + different content         → same_name_conflict
  4. different filename + different content    → ok
  5. legacy row with content_sha1 backfilled   → exact_duplicate on rename
  6. legacy row with content_sha1 NULL         → documented limitation
  7. ACL: inaccessible match must NOT surface
  8. batch endpoint matches single endpoint verdict-for-verdict
  9. multiple accessible rows sharing a hash (existing archive duplicates)
     → exact_duplicate against a stable single reference; no row deleted
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import upload as upload_router
from api import deps


# ── FastAPI app + auth override ─────────────────────────────────────────────
def _make_app(current_user: dict):
    app = FastAPI()
    app.include_router(upload_router.router)
    app.dependency_overrides[deps.get_current_user] = lambda: current_user
    app.dependency_overrides[deps.require_uploader] = lambda: current_user
    return app


def _admin():
    return {"id": 1, "username": "admin", "role": "admin", "dept_id": None, "is_active": 1}


def _uploader(username="alice", dept_id=None):
    return {"id": 2, "username": username, "role": "uploader",
            "dept_id": dept_id, "is_active": 1}


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_db):
    return tmp_db


# ── Cases 1–4: core matrix ─────────────────────────────────────────────────
def test_case1_same_name_same_content_is_exact_duplicate(db):
    db.register_file(doc_id="hashA", source="report.pdf",
                     is_public=True, content_sha1="hashA")

    client = TestClient(_make_app(_admin()))
    r = client.post("/api/upload/check",
                    json={"filename": "report.pdf", "doc_id": "hashA"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "exact_duplicate"
    assert body["existing"]["source"] == "report.pdf"


def test_case2_different_name_same_content_is_exact_duplicate(db):
    """The UPSC bug: renamed byte-identical copy must be detected."""
    db.register_file(doc_id="hashA", source="report.pdf",
                     is_public=True, content_sha1="hashA")

    client = TestClient(_make_app(_admin()))
    r = client.post("/api/upload/check",
                    json={"filename": "report_copy.pdf", "doc_id": "hashA"})
    body = r.json()
    assert body["verdict"] == "exact_duplicate"
    assert body["existing"]["source"] == "report.pdf"


def test_case3_same_name_different_content_is_name_conflict(db):
    db.register_file(doc_id="hashA", source="report.pdf",
                     is_public=True, content_sha1="hashA")

    client = TestClient(_make_app(_admin()))
    r = client.post("/api/upload/check",
                    json={"filename": "report.pdf", "doc_id": "hashB"})
    assert r.json()["verdict"] == "same_name_conflict"


def test_case4_different_name_different_content_is_ok(db):
    db.register_file(doc_id="hashA", source="report.pdf",
                     is_public=True, content_sha1="hashA")

    client = TestClient(_make_app(_admin()))
    r = client.post("/api/upload/check",
                    json={"filename": "unrelated.pdf", "doc_id": "hashB"})
    assert r.json()["verdict"] == "ok"


# ── Case 5: legacy row + backfill ──────────────────────────────────────────
def test_case5_legacy_row_with_backfilled_sha1_detects_rename(db):
    """A legacy:filename doc_id still detects rename after backfill."""
    db.register_file(doc_id="legacy:UPSC.pdf", source="UPSC.pdf",
                     is_public=True, content_sha1=None)
    # Backfill (simulates scripts/backfill_content_sha1.py).
    db.set_content_sha1("legacy:UPSC.pdf", "realsha1hex1234")

    client = TestClient(_make_app(_admin()))
    r = client.post("/api/upload/check",
                    json={"filename": "UPSC Copy.pdf", "doc_id": "realsha1hex1234"})
    body = r.json()
    assert body["verdict"] == "exact_duplicate"
    assert body["existing"]["doc_id"] == "legacy:UPSC.pdf"
    # doc_id must not have been rewritten.
    assert body["existing"]["source"] == "UPSC.pdf"


# ── Case 6: legacy row with unrecoverable bytes ────────────────────────────
def test_case6_legacy_row_without_sha1_documented_limitation(db):
    """
    An unrecoverable legacy row (content_sha1 = NULL) cannot participate
    in content-based dedup. This is documented; the filename branch is the
    only remaining catch.
    """
    db.register_file(doc_id="legacy:Report.pdf", source="Report.pdf",
                     is_public=True, content_sha1=None)

    client = TestClient(_make_app(_admin()))

    # Rename of the same content: cannot be detected → ok. Limitation.
    r = client.post("/api/upload/check",
                    json={"filename": "Renamed.pdf", "doc_id": "somehash"})
    assert r.json()["verdict"] == "ok"

    # Same filename with a new hash still catches via same_name_conflict.
    r = client.post("/api/upload/check",
                    json={"filename": "Report.pdf", "doc_id": "somehash"})
    assert r.json()["verdict"] == "same_name_conflict"


# ── Case 7: ACL scoping ────────────────────────────────────────────────────
def test_case7_acl_inaccessible_match_is_not_revealed(db):
    """
    A user without access to a document must NOT be told it exists by
    uploading identical content. The endpoint must return 'ok', not
    'exact_duplicate', so no info about the inaccessible file leaks.
    """
    # Two departments in the seeded set; the file is tagged only to HR.
    depts = db.list_departments()
    hr = next(d for d in depts if d["name"] == "HR")
    sales = next(d for d in depts if d["name"] == "Sales")

    # Create alice in Sales, bob in HR; the file belongs to HR and is
    # NOT public.
    db.create_user("alice", "pw", role="uploader", dept_id=sales["id"])
    db.create_user("bob", "pw", role="uploader", dept_id=hr["id"])

    db.register_file(doc_id="hashSECRET", source="hr_only.pdf",
                     uploaded_by="bob", dept_ids=[hr["id"]],
                     is_public=False, content_sha1="hashSECRET")

    # Alice uploads a byte-identical copy under a different name.
    alice = _uploader("alice", dept_id=sales["id"])
    client = TestClient(_make_app(alice))
    r = client.post("/api/upload/check",
                    json={"filename": "innocuous.pdf", "doc_id": "hashSECRET"})
    body = r.json()
    # No leak: exact_duplicate would confirm the file's existence.
    assert body["verdict"] == "ok"
    assert body["existing"] is None

    # Also: no filename leak either — alice tries the exact HR filename.
    r = client.post("/api/upload/check",
                    json={"filename": "hr_only.pdf", "doc_id": "differenthash"})
    body = r.json()
    assert body["verdict"] == "ok"
    assert body["existing"] is None


def test_case7b_acl_admin_sees_match(db):
    """Complement: admin bypass returns the exact_duplicate the viewer couldn't."""
    depts = db.list_departments()
    hr = next(d for d in depts if d["name"] == "HR")
    db.register_file(doc_id="hashSECRET", source="hr_only.pdf",
                     dept_ids=[hr["id"]], is_public=False,
                     content_sha1="hashSECRET")

    client = TestClient(_make_app(_admin()))
    r = client.post("/api/upload/check",
                    json={"filename": "any.pdf", "doc_id": "hashSECRET"})
    assert r.json()["verdict"] == "exact_duplicate"


# ── Case 8: batch endpoint parity with single ──────────────────────────────
def test_case8_batch_endpoint_matches_single_verdicts(db):
    db.register_file(doc_id="hashA", source="a.pdf",
                     is_public=True, content_sha1="hashA")
    db.register_file(doc_id="hashB", source="b.pdf",
                     is_public=True, content_sha1="hashB")

    items = [
        {"filename": "a.pdf",       "content_sha1": "hashA"},   # exact same name
        {"filename": "a_copy.pdf",  "content_sha1": "hashA"},   # exact renamed
        {"filename": "a.pdf",       "content_sha1": "hashZZZ"}, # name conflict
        {"filename": "brand_new.pdf","content_sha1": "hashZZZ"},# ok
    ]

    client = TestClient(_make_app(_admin()))

    # Single-endpoint verdicts, one at a time.
    singles = []
    for it in items:
        r = client.post("/api/upload/check",
                        json={"filename": it["filename"], "doc_id": it["content_sha1"]})
        singles.append(r.json()["verdict"])

    # Batch endpoint, one call.
    r = client.post("/api/upload/check-batch", json={"items": items})
    batch = [x["verdict"] for x in r.json()["results"]]

    assert batch == singles
    assert singles == ["exact_duplicate", "exact_duplicate",
                       "same_name_conflict", "ok"]


def test_case8b_batch_preserves_input_order_and_scoping(db):
    """Batch endpoint must be ACL-scoped identically to the single one."""
    depts = db.list_departments()
    hr = next(d for d in depts if d["name"] == "HR")
    sales = next(d for d in depts if d["name"] == "Sales")
    db.create_user("alice", "pw", role="uploader", dept_id=sales["id"])

    db.register_file(doc_id="hashPUB", source="public.pdf",
                     is_public=True, content_sha1="hashPUB")
    db.register_file(doc_id="hashSECRET", source="hr_only.pdf",
                     dept_ids=[hr["id"]], is_public=False,
                     content_sha1="hashSECRET")

    client = TestClient(_make_app(_uploader("alice", dept_id=sales["id"])))
    r = client.post("/api/upload/check-batch", json={"items": [
        {"filename": "any.pdf",     "content_sha1": "hashSECRET"},  # must be ok — hidden
        {"filename": "public.pdf",  "content_sha1": "hashPUB"},     # exact
    ]})
    verdicts = [x["verdict"] for x in r.json()["results"]]
    assert verdicts == ["ok", "exact_duplicate"]


def test_case8c_empty_batch(db):
    client = TestClient(_make_app(_admin()))
    r = client.post("/api/upload/check-batch", json={"items": []})
    assert r.status_code == 200
    assert r.json() == {"results": []}


# ── Case 9: multiple accessible rows with same hash ────────────────────────
def test_case9_multiple_rows_same_hash_returns_stable_reference(db):
    """
    The archive can legitimately contain content-identical files under
    different names. Duplicate detection must return an exact_duplicate
    verdict against a single stable reference — the earliest-created — and
    must never delete or merge existing rows.
    """
    db.register_file(doc_id="hashA-1", source="first.pdf",
                     is_public=True, content_sha1="hashA")
    db.register_file(doc_id="hashA-2", source="second.pdf",
                     is_public=True, content_sha1="hashA")

    client = TestClient(_make_app(_admin()))
    r1 = client.post("/api/upload/check",
                     json={"filename": "third.pdf", "doc_id": "hashA"})
    r2 = client.post("/api/upload/check",
                     json={"filename": "third.pdf", "doc_id": "hashA"})
    assert r1.json()["verdict"] == "exact_duplicate"
    assert r2.json()["verdict"] == "exact_duplicate"
    # Stable — same reference on both calls.
    assert r1.json()["existing"]["doc_id"] == r2.json()["existing"]["doc_id"]
    # Neither row deleted.
    all_docs = {f["doc_id"] for f in db.list_files()}
    assert {"hashA-1", "hashA-2"}.issubset(all_docs)


# ── doc_id is never touched by any duplicate-detection code path ────────────
def test_doc_id_unchanged_after_checks(db):
    db.register_file(doc_id="legacy:X.pdf", source="X.pdf",
                     is_public=True, content_sha1=None)
    db.set_content_sha1("legacy:X.pdf", "realhash")

    client = TestClient(_make_app(_admin()))
    for _ in range(3):
        client.post("/api/upload/check",
                    json={"filename": "renamed.pdf", "doc_id": "realhash"})
        client.post("/api/upload/check-batch", json={"items": [
            {"filename": "renamed.pdf", "content_sha1": "realhash"},
        ]})

    row = next(f for f in db.list_files() if f["source"] == "X.pdf")
    assert row["doc_id"] == "legacy:X.pdf"
    assert row["content_sha1"] == "realhash"


# ── hidden_by_admin rows are excluded from lookups ─────────────────────────
def test_hidden_by_admin_excluded_from_dedup(db):
    """
    A row hidden by admin (soft-delete used by 'Replace existing') must not
    surface as an exact_duplicate — otherwise re-uploading after a Replace
    would immediately be blocked as a duplicate.
    """
    db.register_file(doc_id="hashA", source="old.pdf",
                     is_public=True, content_sha1="hashA")
    db.set_file_flags("hashA", hidden_by_admin=True)

    client = TestClient(_make_app(_admin()))
    r = client.post("/api/upload/check",
                    json={"filename": "new.pdf", "doc_id": "hashA"})
    assert r.json()["verdict"] == "ok"
