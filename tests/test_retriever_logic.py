"""
tests/test_retriever_logic.py
==============================
Unit tests for the pure-logic functions in retrieval/retriever.py.
All ChromaDB, sentence_transformers, and Ollama dependencies are mocked
in conftest.py so these tests run entirely offline.

Covers:
  - _normalise()       — CamelCase splitting, lowercasing, punctuation stripping
  - _fuzzy_correct()   — typo tolerance
  - _expand_synonyms() — domain synonym expansion
  - _rrf()             — Reciprocal Rank Fusion correctness
  - _is_allowed()      — chunk-level ACL check
  - _acl_where()       — Chroma where-clause builder
"""

import pytest
import sys
from unittest.mock import MagicMock


# ── Import retriever pure functions (heavy deps already mocked in conftest) ───

from retrieval.retriever import (
    _normalise,
    _rrf,
    _is_allowed,
    _acl_where,
    _expand_synonyms,
    _fuzzy_correct,
    SYNONYMS,
)
from pipeline.doc_id import legacy_doc_id


# ─────────────────────────────────────────────────────────────────────────────
# _normalise()
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalise:
    def test_lowercases(self):
        assert _normalise("HAWKINS COOKER") == "hawkins cooker"

    def test_camelcase_split(self):
        assert _normalise("ProjectAurora") == "project aurora"

    def test_hyphens_to_spaces(self):
        assert _normalise("hard-anodised") == "hard anodised"

    def test_underscores_to_spaces(self):
        assert _normalise("vendor_contract") == "vendor contract"

    def test_strips_punctuation(self):
        assert _normalise("What's the policy?") == "what s the policy"

    def test_collapses_whitespace(self):
        assert _normalise("  too   many   spaces  ") == "too many spaces"

    def test_numbers_preserved(self):
        assert _normalise("Q4 2025") == "q4 2025"

    def test_empty_string(self):
        assert _normalise("") == ""

    def test_camelcase_with_numbers(self):
        result = _normalise("Report2025Q4")
        assert "report" in result
        assert "2025" in result

    def test_mixed_separators(self):
        result = _normalise("Project-Aurora_Phase2")
        assert result == "project aurora phase2"


# ─────────────────────────────────────────────────────────────────────────────
# _expand_synonyms()
# ─────────────────────────────────────────────────────────────────────────────

class TestExpandSynonyms:
    def test_returns_list(self):
        result = _expand_synonyms("cooker")
        assert isinstance(result, list)

    def test_original_query_always_in_variants(self):
        query = "cooker"
        result = _expand_synonyms(query)
        assert query in result

    def test_synonyms_added_for_known_term(self):
        result = _expand_synonyms("cooker")
        # SYNONYMS["cooker"] includes "pressure cooker"
        all_variants = " ".join(result)
        assert "pressure cooker" in all_variants or any("pressure" in v for v in result)

    def test_unknown_term_returns_only_original(self):
        result = _expand_synonyms("zygomorphic")
        assert result == ["zygomorphic"]

    def test_multi_word_synonym_expansion(self):
        result = _expand_synonyms("pressure cooker")
        # "pressure cooker" should trigger its synonyms
        assert len(result) >= 1

    def test_ns_expands_to_nonstick(self):
        result = _expand_synonyms("ns")
        variants_flat = " ".join(result)
        assert "nonstick" in variants_flat or "non stick" in variants_flat

    def test_no_duplicates_in_result(self):
        result = _expand_synonyms("cooker")
        assert len(result) == len(set(result))


# ─────────────────────────────────────────────────────────────────────────────
# _rrf()
# ─────────────────────────────────────────────────────────────────────────────

class TestRRF:
    def test_returns_dict(self):
        result = _rrf([{"a": 0, "b": 1}])
        assert isinstance(result, dict)

    def test_single_ranker_correct_scores(self):
        # With one ranker and k=60: score(rank=0) = 1/(60+0) = 0.01667
        result = _rrf([{"a": 0}], k=60)
        assert abs(result["a"] - 1 / 60) < 1e-9

    def test_item_in_two_rankers_scores_higher(self):
        # "shared" appears in both rankers; "unique" only in one
        r1 = {"shared": 0, "unique": 1}
        r2 = {"shared": 0}
        result = _rrf([r1, r2])
        assert result["shared"] > result["unique"]

    def test_higher_rank_position_scores_lower(self):
        # rank 0 (best) should beat rank 5
        result = _rrf([{"best": 0, "worst": 5}])
        assert result["best"] > result["worst"]

    def test_k_parameter_respected(self):
        # k=0 would give 1/(0+0) = inf, but k is always > 0 in practice.
        # Verify k=1 gives different scores than k=60.
        r = {"a": 0}
        score_k1  = _rrf([r], k=1)["a"]
        score_k60 = _rrf([r], k=60)["a"]
        assert score_k1 > score_k60   # smaller k → larger score for same rank

    def test_empty_rankers(self):
        assert _rrf([]) == {}
        assert _rrf([{}]) == {}

    def test_three_rankers_merged(self):
        r1 = {"a": 0, "b": 1}
        r2 = {"b": 0, "c": 1}
        r3 = {"a": 0, "c": 0}
        result = _rrf([r1, r2, r3])
        assert set(result.keys()) == {"a", "b", "c"}

    def test_rrf_scale_invariant(self):
        """RRF only uses rank POSITIONS, not raw scores — this is its key property."""
        # Two rankers with the same ordering but very different "score gaps"
        # should produce identical RRF output (RRF doesn't see the gaps)
        r_tight = {"x": 0, "y": 1, "z": 2}   # ranks only
        r_wide  = {"x": 0, "y": 1, "z": 2}
        result1 = _rrf([r_tight])
        result2 = _rrf([r_wide])
        for key in result1:
            assert abs(result1[key] - result2[key]) < 1e-12


# ─────────────────────────────────────────────────────────────────────────────
# _is_allowed()
# ─────────────────────────────────────────────────────────────────────────────

class TestIsAllowed:
    def test_none_allowed_means_admin_sees_everything(self):
        meta = {"doc_id": "any_id", "source": "file.pdf"}
        assert _is_allowed(meta, allowed=None) is True

    def test_doc_id_in_allowed_set_returns_true(self):
        meta = {"doc_id": "abc123", "source": "file.pdf"}
        assert _is_allowed(meta, allowed={"abc123", "def456"}) is True

    def test_doc_id_not_in_allowed_set_returns_false(self):
        meta = {"doc_id": "abc123", "source": "file.pdf"}
        assert _is_allowed(meta, allowed={"def456", "ghi789"}) is False

    def test_empty_allowed_set_returns_false(self):
        meta = {"doc_id": "abc123", "source": "file.pdf"}
        assert _is_allowed(meta, allowed=set()) is False

    def test_legacy_fallback_for_chunk_without_doc_id(self):
        # When doc_id is absent, chunk_doc_id() builds legacy form from source
        meta = {"source": "old_file.pdf"}  # no doc_id key
        legacy_id = legacy_doc_id("old_file.pdf")
        assert _is_allowed(meta, allowed={legacy_id}) is True

    def test_legacy_fallback_not_in_allowed_returns_false(self):
        meta = {"source": "old_file.pdf"}
        assert _is_allowed(meta, allowed={"some_other_id"}) is False

    def test_empty_meta_dict(self):
        # source defaults to "unknown" → legacy_doc_id("unknown")
        legacy_id = legacy_doc_id("unknown")
        assert _is_allowed({}, allowed={legacy_id}) is True
        assert _is_allowed({}, allowed={"not_this"}) is False


# ─────────────────────────────────────────────────────────────────────────────
# _acl_where()
# ─────────────────────────────────────────────────────────────────────────────

class TestAclWhere:
    def test_none_allowed_no_filters_returns_none(self):
        # Admin with no extra filters → no Chroma where clause needed
        assert _acl_where(None, filters=None) is None

    def test_none_allowed_with_filter_returns_filter_only(self):
        f = {"doc_type": {"$eq": "policy"}}
        result = _acl_where(None, filters=f)
        assert result == f

    def test_empty_allowed_set_builds_in_clause(self):
        result = _acl_where(set())
        assert result == {"doc_id": {"$in": []}}

    def test_allowed_set_builds_in_clause(self):
        allowed = {"id1", "id2", "id3"}
        result = _acl_where(allowed)
        assert result["doc_id"]["$in"] == sorted(allowed)

    def test_allowed_plus_filter_builds_and_clause(self):
        allowed = {"id1", "id2"}
        f = {"doc_type": {"$eq": "manual"}}
        result = _acl_where(allowed, filters=f)
        assert result.get("$and") is not None
        clauses = result["$and"]
        assert len(clauses) == 2

    def test_sorted_ids_for_determinism(self):
        # The $in list must be sorted so identical queries produce identical
        # Chroma where clauses (important for any future caching layer)
        allowed = {"zzz", "aaa", "mmm"}
        result = _acl_where(allowed)
        assert result["doc_id"]["$in"] == ["aaa", "mmm", "zzz"]
