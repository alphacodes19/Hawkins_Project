import os

# Base directory — everything is relative to this
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Data paths
DATA_RAW        = os.path.join(BASE_DIR, "data", "raw")
DATA_PROCESSED  = os.path.join(BASE_DIR, "data", "processed")

# Sub-folders inside data/raw
PDF_DIR    = os.path.join(DATA_RAW, "pdfs")
DOCX_DIR   = os.path.join(DATA_RAW, "docx")
EXCEL_DIR  = os.path.join(DATA_RAW, "excel")
EMAIL_DIR  = os.path.join(DATA_RAW, "emails")
SQL_DIR    = os.path.join(DATA_RAW, "sql")

# Chunking settings
CHUNK_SIZE    = 512   # words per chunk
CHUNK_OVERLAP = 50    # words overlap between chunks

# Embedding model (runs locally, no internet needed after first download)
EMBEDDING_MODEL = "BAAI/bge-m3"

# ChromaDB ( vector database)
CHROMA_PATH       = os.path.join(BASE_DIR, "chroma_db")
CHROMA_COLLECTION = "hawkins_knowledge"

# Ollama LLM settings
OLLAMA_MODEL    = "llama3.1:8b"
OLLAMA_BASE_URL = "http://localhost:11434"

# How many chunks to retrieve per query
TOP_K_RESULTS = 5

# World bible path
WORLD_BIBLE_PATH = os.path.join(BASE_DIR, "world_bible.json")