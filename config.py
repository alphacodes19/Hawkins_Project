import os

# Base directory — everything is relative to this
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Data paths
DATA_RAW        = os.path.join(BASE_DIR, "data", "raw")
DATA_PROCESSED  = os.path.join(BASE_DIR, "data", "processed")

# Sub-folders inside data/raw (synthetic data)
PDF_DIR    = os.path.join(DATA_RAW, "pdfs")
DOCX_DIR   = os.path.join(DATA_RAW, "docx")
EXCEL_DIR  = os.path.join(DATA_RAW, "excel")
EMAIL_DIR  = os.path.join(DATA_RAW, "emails")
SQL_DIR    = os.path.join(DATA_RAW, "sql")

# Sub-folders inside data/real_sourced (real Hawkins documents)
REAL_PDF_DIR  = os.path.join(BASE_DIR, "data", "real_sourced", "pdfs")
REAL_DOCX_DIR = os.path.join(BASE_DIR, "data", "real_sourced", "docx")

# ── Chunking ──────────────────────────────────────────────────────────────────
# Reduced from 512 → 300 words.
#
# WHY: the ms-marco-MiniLM-L-6-v2 reranker has a hard 512 TOKEN limit.
# 512 words ≈ 680 tokens — the reranker was silently truncating every chunk.
# 300 words ≈ 400 tokens — safely within the limit, full chunk is scored.
# This requires a full re-index (python -m pipeline.indexer --reset).
CHUNK_SIZE    = 300   # words per chunk  (was 512)
CHUNK_OVERLAP = 50    # words overlap between chunks

# ── Embedding model ───────────────────────────────────────────────────────────
EMBEDDING_MODEL = "BAAI/bge-m3"

# ── ChromaDB ──────────────────────────────────────────────────────────────────
CHROMA_PATH       = os.path.join(BASE_DIR, "chroma_db")
CHROMA_COLLECTION = "hawkins_knowledge"

# ── Ollama LLM ────────────────────────────────────────────────────────────────
# Upgraded from llama3.1:8b → qwen2.5:7b
#
# WHY qwen2.5:7b on this server (128GB RAM, CPU-only, Windows Server):
#   - 128GB RAM comfortably fits the ~20GB model footprint
#   - Better than LLaMA at staying grounded in provided documents (less hallucination)
#   - Better structured document understanding (tables, lists, technical specs)
#   - Better multilingual support for any Hindi content in Hawkins docs
#   - CPU inference: ~15-25 seconds per answer (acceptable for internal tool)
#
# To upgrade further to 72b (if 25s answers are acceptable):
#   ollama pull qwen2.5:72b-q4_K_M
#   OLLAMA_MODEL = "qwen2.5:72b-q4_K_M"   (~40GB RAM, better quality)
#
# Pull command:  ollama pull qwen2.5:7b
OLLAMA_MODEL    = "qwen2.5:14b"
OLLAMA_BASE_URL = "http://localhost:11434"

# ── Context window ────────────────────────────────────────────────────────────
# Raised from 8192 → 16384 tokens.
# The larger model can use a larger context window, so we feed it more document
# text before it generates an answer.
OLLAMA_NUM_CTX = 8192

# ── Retrieval ─────────────────────────────────────────────────────────────────
# How many chunks to retrieve in the fallback vector-only path
TOP_K_RESULTS = 5

# How many docs to feed to the AI answer (raised from 5 → 8)
# How many chunks per doc to feed (raised from 1 → 2)
# Together: 8 docs × 2 chunks × ~300 words ≈ 4,800 words ≈ 6,400 tokens
# Well within the 16,384 token context window.
ANSWER_TOP_DOCS   = 5
ANSWER_CHUNKS_PER_DOC = 2

# How many candidates to fetch before reranking (raised for better recall)
VECTOR_CANDIDATES = 200   # was 100
BM25_CANDIDATES   = 300   # was 150

# World bible path (currently unused — placeholder for future use)
# Lives in generation/ alongside the synthetic-data generator that reads
# it, not the project root — moved there during a repo cleanup.
WORLD_BIBLE_PATH = os.path.join(BASE_DIR, "generation", "world_bible.json")