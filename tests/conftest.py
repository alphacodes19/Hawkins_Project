"""
conftest.py — shared fixtures and sys.path setup for all tests
==============================================================
Heavy ML/DB dependencies (chromadb, sentence_transformers, ollama) are
mocked at the module level here so tests run offline without any model
downloads, GPU, or running Ollama instance.

Tests that need the full integration (live ChromaDB, live Ollama) are
marked with @pytest.mark.integration and are excluded from CI by default:
    pytest -m "not integration"
"""

import os
import sys
import pytest
from unittest.mock import MagicMock

# ── Make the project root importable ────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Mock heavy dependencies before any project module is imported ────────────
# These are installed on the real server but not in CI / offline test envs.
_MOCK_MODULES = [
    "chromadb",
    "chromadb.config",
    "sentence_transformers",
    "ollama",
    "pypdfium2",
    "pytesseract",
    "pdfplumber",
    "docx",
    "openpyxl",
    "rank_bm25",
]

for _mod in _MOCK_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Mock pipeline.embedder so retriever imports without downloading BGE-M3
_embedder_mock = MagicMock()
_embedder_mock.embed_text = lambda text: [0.0] * 1024
_embedder_mock.get_model = MagicMock(return_value=MagicMock())
sys.modules["pipeline.embedder"] = _embedder_mock

# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    An isolated auth.db in a temp directory.
    Patches config.BASE_DIR so every auth.db call writes to tmp_path.
    """
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    # auth.db uses config.BASE_DIR at import time for DB_PATH,
    # so patch the module-level constant too.
    import auth.db as authdb
    monkeypatch.setattr(authdb, "DB_PATH", str(tmp_path / "auth.db"))
    authdb.init_db()
    return authdb
