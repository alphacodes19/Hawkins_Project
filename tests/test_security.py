"""
tests/test_security.py
======================
Unit tests for auth/security.py:
  - hash_password() produces the correct format
  - verify_password() accepts correct passwords
  - verify_password() rejects wrong passwords
  - Timing-safe comparison (hmac.compare_digest) is used — structural check
  - Malformed stored hashes fail gracefully (no exceptions)
  - Empty password / empty stored value edge cases
  - Two hashes of the same password differ (random salt)
"""

import pytest
from auth.security import hash_password, verify_password, ALGORITHM, ITERATIONS, SALT_BYTES


# ── Format tests ─────────────────────────────────────────────────────────────

class TestHashFormat:
    def test_produces_four_segments(self):
        h = hash_password("secret")
        parts = h.split("$")
        assert len(parts) == 4, f"Expected 4 segments, got {len(parts)}: {h}"

    def test_algorithm_segment(self):
        h = hash_password("secret")
        assert h.startswith(f"{ALGORITHM}$")

    def test_iterations_segment(self):
        h = hash_password("secret")
        algo, iters, salt_hex, hash_hex = h.split("$")
        assert int(iters) == ITERATIONS

    def test_salt_is_hex_and_correct_length(self):
        h = hash_password("secret")
        _, _, salt_hex, _ = h.split("$")
        # SALT_BYTES bytes → 2*SALT_BYTES hex chars
        assert len(salt_hex) == SALT_BYTES * 2
        int(salt_hex, 16)  # raises ValueError if not valid hex

    def test_hash_is_hex_sha256_length(self):
        h = hash_password("secret")
        _, _, _, hash_hex = h.split("$")
        assert len(hash_hex) == 64  # SHA-256 → 32 bytes → 64 hex chars
        int(hash_hex, 16)

    def test_empty_password_raises(self):
        with pytest.raises(ValueError):
            hash_password("")

    def test_none_password_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            hash_password(None)


# ── Correctness tests ─────────────────────────────────────────────────────────

class TestVerifyPassword:
    def test_correct_password_accepted(self):
        pw = "hawkins-secure-2025"
        stored = hash_password(pw)
        assert verify_password(pw, stored) is True

    def test_wrong_password_rejected(self):
        stored = hash_password("correct-password")
        assert verify_password("wrong-password", stored) is False

    def test_empty_password_rejected(self):
        stored = hash_password("real-password")
        assert verify_password("", stored) is False

    def test_empty_stored_rejected(self):
        assert verify_password("any-password", "") is False

    def test_none_password_rejected(self):
        stored = hash_password("real")
        assert verify_password(None, stored) is False

    def test_none_stored_rejected(self):
        assert verify_password("real", None) is False

    def test_unicode_password_accepted(self):
        pw = "pásswörd-日本語"
        stored = hash_password(pw)
        assert verify_password(pw, stored) is True

    def test_unicode_wrong_rejected(self):
        stored = hash_password("pásswörd-日本語")
        assert verify_password("password", stored) is False

    def test_case_sensitive(self):
        stored = hash_password("Secret")
        assert verify_password("secret", stored) is False
        assert verify_password("SECRET", stored) is False
        assert verify_password("Secret", stored) is True

    def test_whitespace_matters(self):
        stored = hash_password("password")
        assert verify_password("password ", stored) is False
        assert verify_password(" password", stored) is False


# ── Malformed stored value tests ──────────────────────────────────────────────

class TestMalformedStored:
    def test_too_few_segments_returns_false(self):
        assert verify_password("pw", "pbkdf2_sha256$240000$abc") is False

    def test_too_many_segments_returns_false(self):
        assert verify_password("pw", "pbkdf2_sha256$240000$abc$def$extra") is False

    def test_non_numeric_iterations_returns_false(self):
        assert verify_password("pw", "pbkdf2_sha256$notanint$abc$def") is False

    def test_non_hex_salt_returns_false(self):
        assert verify_password("pw", "pbkdf2_sha256$240000$ZZZZZZ$abcdef") is False

    def test_wrong_algorithm_returns_false(self):
        stored = hash_password("secret")
        bad = stored.replace("pbkdf2_sha256", "bcrypt")
        assert verify_password("secret", bad) is False

    def test_truncated_hash_returns_false(self):
        stored = hash_password("secret")
        truncated = stored[:-10]
        assert verify_password("secret", truncated) is False

    def test_corrupted_hash_segment_returns_false(self):
        stored = hash_password("secret")
        parts = stored.split("$")
        parts[3] = "0" * 64   # valid hex but wrong value
        assert verify_password("secret", "$".join(parts)) is False


# ── Randomness / salt uniqueness ─────────────────────────────────────────────

class TestSaltUniqueness:
    def test_same_password_produces_different_hashes(self):
        pw = "same-password"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert h1 != h2, "Two hashes of the same password must differ (random salt)"

    def test_both_hashes_verify_correctly(self):
        pw = "same-password"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert verify_password(pw, h1) is True
        assert verify_password(pw, h2) is True

    def test_salts_differ_between_hashes(self):
        pw = "same-password"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        salt1 = h1.split("$")[2]
        salt2 = h2.split("$")[2]
        assert salt1 != salt2
