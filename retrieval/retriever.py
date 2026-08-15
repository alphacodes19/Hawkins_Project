"""
retriever.py — Hybrid Retrieval Pipeline
==========================================
ORIGINAL retrieve() function is kept exactly as-is.
  → used by stream_answer() in app.py for the AI answer.

NEW retrieve_documents() function added on top.
  → used by the document results panel in app.py.
  → returns top-20 unique documents ranked by relevance.

Pipeline:
  1. Query normalisation   (lowercase, CamelCase split, punctuation strip)
  2. Fuzzy correction      (typo tolerance via rapidfuzz)
  3. Synonym expansion     (domain-specific dictionary)
  4. BM25 lexical search   (exact/keyword matching, word-order independent)
  5. Dense vector search   (semantic / meaning-based, original behaviour)
  6. Metadata search       (filename, doc_type, department, project fuzzy match)
  7. RRF merge             (Reciprocal Rank Fusion of all three signals)
  8. Cross-encoder rerank  (reorder top candidates by true relevance)
  9. Document grouping     (deduplicate chunks → unique files, top 20)
 10. Score threshold       (drop irrelevant tail docs)
"""

import os
import re
import math
import chromadb
import config
from pipeline.embedder import embed_text
from pipeline.doc_id import chunk_doc_id

# ── lazy imports (heavy deps only loaded when first needed) ──────────────────
_bm25         = None
_bm25_corpus  = None
_meta_cache   = None   # {cid: normalised_meta_string} — built with BM25, invalidated together
_reranker     = None
_collection   = None


# ─────────────────────────────────────────────────────────────────────────────
# SYNONYM DICTIONARY
# ─────────────────────────────────────────────────────────────────────────────
SYNONYMS = {
    "cooker":          ["pressure cooker", "hawkins cooker", "futura"],
    "pressure cooker": ["cooker", "pc", "futura pc"],
    "pot":             ["handi", "patila", "pressure cooker", "casserole"],
    "pan":             ["tawa", "tava", "frying pan", "kadhai", "wok"],
    "tawa":            ["tava", "griddle", "flat pan", "dosa pan"],
    "kadhai":          ["kadai", "wok", "deep pan", "karahi"],
    "handi":           ["pot", "biryani pot", "patila", "casserole"],
    "bill":            ["invoice", "receipt", "payment"],
    "invoice":         ["bill", "receipt", "payment record"],
    "catalogue":       ["catalog", "product list", "price list", "brochure"],
    "catalog":         ["catalogue", "product list", "price list"],
    "price list":      ["pricelist", "pricing", "rate list", "catalogue"],
    "contract":        ["agreement", "supply agreement", "vendor contract"],
    "agreement":       ["contract", "mou", "supply contract"],
    "vendor":          ["supplier", "partner", "manufacturer"],
    "supplier":        ["vendor", "partner"],
    "report":          ["summary", "review", "analysis", "findings"],
    "policy":          ["guideline", "rule", "procedure", "standard"],
    "manual":          ["instruction", "guide", "handbook", "im"],
    "specification":   ["spec", "features", "details", "datasheet"],
    "recipe":          ["dish", "food", "cooking", "how to make", "ingredients"],
    "poha":            ["pohe", "beaten rice", "flattened rice"],
    "pohe":            ["poha", "beaten rice"],
    "dosa":            ["dosai", "crepe", "thin pancake"],
    "biryani":         ["rice dish", "pulao", "pilaf"],
    "roti":            ["chapati", "phulka", "bread", "flatbread"],
    "paratha":         ["flatbread", "stuffed roti"],
    "im":              ["instruction manual", "manual"],
    "pc":              ["pressure cooker"],
    "ns":              ["nonstick", "non stick", "non-stick"],
    "nonstick":        ["ns", "non stick", "non-stick"],
    "ha":              ["hard anodised", "hard anodized"],
    "hard anodised":   ["ha", "hard anodized", "anodised"],
    "ss":              ["stainless steel"],
    "stainless steel": ["ss", "steel"],
    "induction":       ["induction compatible", "ic", "induction base"],
    "tri-ply":         ["triply", "tri ply", "3 ply", "three ply"],
    "die cast":        ["die-cast", "diecast", "cast"],
}

KNOWN_TERMS = list(set(
    list(SYNONYMS.keys()) +
    [s for syns in SYNONYMS.values() for s in syns] + [
        "hawkins", "futura", "bigboy", "contura", "hevibase", "ventura",
        "instaa", "cerenity", "missmary", "ironman", "aqua", "quik",
        "biryani", "kadhai", "tawa", "handi", "casserole", "wok",
        "saucepan", "frying pan", "dosa tava", "uttapam", "idli",
        "appe", "dutch oven", "pizza maker", "kettle", "induction",
        "project aurora", "project falcon", "project helix", "project horizon",
        "presstek", "safeseal", "steelform", "germancoat", "rosewood",
        "packrite", "ferro alloys", "ceracoat", "tristar",
    ]
))


# ─────────────────────────────────────────────────────────────────────────────
# 1. QUERY NORMALISATION
# ─────────────────────────────────────────────────────────────────────────────
def _normalise(text: str) -> str:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)   # CamelCase → words
    text = text.lower()
    text = re.sub(r"[-_]", " ", text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ─────────────────────────────────────────────────────────────────────────────
# 2. FUZZY CORRECTION
# ─────────────────────────────────────────────────────────────────────────────
def _fuzzy_correct(query: str) -> str:
    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        return query
    tokens, corrected = query.split(), []
    for token in tokens:
        if token in KNOWN_TERMS or len(token) <= 3:
            corrected.append(token)
            continue
        match = process.extractOne(token, KNOWN_TERMS, scorer=fuzz.ratio)
        corrected.append(match[0] if match and match[1] >= 80 else token)
    return " ".join(corrected)


# ─────────────────────────────────────────────────────────────────────────────
# 3. SYNONYM EXPANSION
# ─────────────────────────────────────────────────────────────────────────────
def _expand_synonyms(query: str) -> list:
    variants = {query}
    tokens = query.split()
    for token in tokens:
        for syn in SYNONYMS.get(token, []):
            variants.add(syn)
    for n in range(2, 4):
        for i in range(len(tokens) - n + 1):
            phrase = " ".join(tokens[i:i+n])
            for syn in SYNONYMS.get(phrase, []):
                variants.add(syn)
    return list(variants)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED COLLECTION
# ─────────────────────────────────────────────────────────────────────────────
def get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        _collection = client.get_collection(config.CHROMA_COLLECTION)
    return _collection


# ─────────────────────────────────────────────────────────────────────────────
# 4. BM25 INDEX  (lazy build, invalidated after new uploads)
# ─────────────────────────────────────────────────────────────────────────────
def _build_bm25():
    global _bm25, _bm25_corpus
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return
    collection = get_collection()

    # Fetch in batches to avoid ChromaDB "too many SQL variables" error
    # which occurs when collection has tens of thousands of chunks.
    BATCH = 5000
    total = collection.count()
    all_ids, all_docs, all_metas = [], [], []
    for offset in range(0, total, BATCH):
        batch = collection.get(
            include=["documents", "metadatas"],
            limit=BATCH,
            offset=offset,
        )
        all_ids.extend(batch["ids"])
        all_docs.extend(batch["documents"])
        all_metas.extend(batch["metadatas"])

    norm_texts, orig_texts, corpus_meta, corpus_ids = [], [], [], []
    for cid, doc, meta in zip(all_ids, all_docs, all_metas):
        meta = meta or {}
        meta_text = " ".join(filter(None, [
            meta.get("source", ""), meta.get("doc_type", ""),
            meta.get("department", ""), meta.get("project", ""),
            meta.get("date", ""), meta.get("summary", ""),
        ]))
        norm_texts.append(_normalise(doc + " " + meta_text))
        orig_texts.append(doc)
        corpus_meta.append(meta)
        corpus_ids.append(cid)
    _bm25        = BM25Okapi([t.split() for t in norm_texts])
    _bm25_corpus = list(zip(norm_texts, orig_texts, corpus_meta, corpus_ids))
    global _meta_cache
    _meta_cache = {
        cid: (
            _normalise(" ".join(filter(None, [
                m.get("source", ""), m.get("doc_type", ""),
                m.get("department", ""), m.get("project", ""),
                m.get("summary", ""),
            ]))),
            chunk_doc_id(m),
        )
        for cid, m in zip(corpus_ids, corpus_meta)
    }


def invalidate_bm25():
    """Call after indexing new documents so BM25 and metadata cache rebuild on next search."""
    global _bm25, _bm25_corpus, _meta_cache
    _bm25        = None
    _bm25_corpus = None
    _meta_cache  = None

def ensure_bm25_ready():
    if _bm25 is None:
        _build_bm25()


# ─────────────────────────────────────────────────────────────────────────────
# ACCESS CONTROL HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _is_allowed(meta, allowed):
    """allowed=None means admin (no filter). allowed=set() means nothing visible."""
    if allowed is None:
        return True
    return chunk_doc_id(meta or {}) in allowed


def _acl_where(allowed, filters=None):
    """Build Chroma where clause combining ACL and any extra filters."""
    clauses = []
    if allowed is not None:
        clauses.append({"doc_id": {"$in": sorted(allowed)}})
    if filters:
        clauses.append(filters)
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


# ─────────────────────────────────────────────────────────────────────────────
# EXHAUSTIVE KEYWORD SCAN
# ─────────────────────────────────────────────────────────────────────────────
# Scans every chunk in the corpus for the exact query string (case-insensitive).
# Returns:
#   guaranteed_ids  — set of chunk IDs that contain the keyword, injected into
#                     RRF so they are never silently dropped by retrieval ranking
#   keyword_sources — dict {source_filename: chunk_count} for the coverage
#                     counter shown in the UI ("keyword found in N files")
#
# Uses _bm25_corpus if already built (zero extra collection.get() calls).
# Falls back to collection.get() on first call before BM25 is ready.
# ─────────────────────────────────────────────────────────────────────────────
def _exact_keyword_scan(query: str, allowed=None) -> tuple:
    query_lower     = query.lower().strip()
    guaranteed_ids  = set()
    keyword_sources = {}

    # Fast path — reuse in-memory BM25 corpus
    if _bm25_corpus is not None:
        for norm_text, orig_text, meta, cid in _bm25_corpus:
            if not _is_allowed(meta, allowed):
                continue
            if query_lower in orig_text.lower():
                guaranteed_ids.add(cid)
                src = meta.get("source", "unknown")
                keyword_sources[src] = keyword_sources.get(src, 0) + 1
        return guaranteed_ids, keyword_sources

    # Slow path — batch fetch to avoid SQL variables limit
    collection = get_collection()
    BATCH = 5000
    total = collection.count()
    for offset in range(0, total, BATCH):
        batch = collection.get(
            include=["documents", "metadatas"],
            limit=BATCH,
            offset=offset,
        )
        for cid, doc, meta in zip(batch["ids"], batch["documents"], batch["metadatas"]):
            if not _is_allowed(meta, allowed):
                continue
            if query_lower in doc.lower():
                guaranteed_ids.add(cid)
                src = (meta or {}).get("source", "unknown")
                keyword_sources[src] = keyword_sources.get(src, 0) + 1

    return guaranteed_ids, keyword_sources


# ─────────────────────────────────────────────────────────────────────────────
# 5. METADATA SEARCH  (cache-backed — no collection.get() per query)
# ─────────────────────────────────────────────────────────────────────────────
def _metadata_search(query: str, top_n: int = 30, allowed=None) -> dict:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return {}
    if _meta_cache is None:
        _build_bm25()
    if not _meta_cache:
        return {}
    scores = {}
    for cid, (meta_str, did) in _meta_cache.items():
        if allowed is not None and did not in allowed:
            continue
        ratio = fuzz.partial_ratio(query, meta_str) / 100.0
        if ratio >= 0.6:
            scores[cid] = ratio
    sorted_ids = sorted(scores, key=scores.get, reverse=True)[:top_n]
    return {cid: scores[cid] for cid in sorted_ids}


# ─────────────────────────────────────────────────────────────────────────────
# 6. RRF MERGE
# ─────────────────────────────────────────────────────────────────────────────
def _rrf(rank_lists: list, k: int = 60) -> dict:
    scores = {}
    for rank_dict in rank_lists:
        for cid, rank in rank_dict.items():
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return scores


# ─────────────────────────────────────────────────────────────────────────────
# 7. CROSS-ENCODER RERANK
# ─────────────────────────────────────────────────────────────────────────────
def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512)
    return _reranker


def _rerank(query: str, chunks: list, top_n: int = 30) -> list:
    candidates = chunks[:40]
    if not candidates:
        return []
    try:
        reranker = _get_reranker()
        # cross-encoder max_length=512 is in TOKENS (~4 chars/token → 2000 chars ≈ 500 tokens)
        pairs    = [(query, c["text"][:2000]) for c in candidates]
        scores   = reranker.predict(pairs)
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(round(s, 4))
        return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)[:top_n]
    except Exception:
        # reranker not installed — fall back to RRF order
        return candidates[:top_n]


# ─────────────────────────────────────────────────────────────────────────────
# SCORE → PERCENTAGE  (absolute mapping, not relative to best result)
# ─────────────────────────────────────────────────────────────────────────────
# Cross-encoder scores (ms-marco-MiniLM-L-6-v2) are logits in roughly [-10, 10].
# We map them to [0, 100] with a sigmoid-style clamp so a poor match shows a
# genuinely low % rather than being inflated to look relevant.
# When the reranker is skipped (fallback), best_score is an RRF float ≈ [0, 0.05];
# we scale those linearly into [0, 100] using the known RRF ceiling.
def _score_to_pct(score: float) -> int:
    """
    THIS FUNCTION IS NO LONGER USED FOR DISPLAY.
    Kept for backward compatibility only.
    Relevance % is now assigned by rank position in retrieve_documents()
    so the displayed % always matches the visual ranking order.
    """
    if score > 1.0 or score < 0.0:
        sig = 1.0 / (1.0 + math.exp(-(score - 3.0)))
        pct = int(sig * 100)
    else:
        pct = int(min(score / 0.05, 1.0) * 100)
    return max(1, min(100, pct))


# ─────────────────────────────────────────────────────────────────────────────
# NEW: retrieve_documents()  ← document results panel in app.py
# Returns top-20 unique documents with relevance %, snippets, metadata.
# ─────────────────────────────────────────────────────────────────────────────
def retrieve_documents(query: str, filters: dict = None,
                       top_n_docs: int = 20, use_reranker: bool = True,
                       allowed_doc_ids=None) -> tuple:
    """
    Returns (docs, coverage).
    allowed_doc_ids: set of doc_ids user may see, or None for admin (no filter).
    """
    allowed = allowed_doc_ids

    if allowed is not None and not allowed:
        return [], {"keyword_file_count": 0, "keyword_chunk_count": 0, "keyword_sources": {}}

    norm      = _normalise(query)
    corrected = _fuzzy_correct(norm)
    variants  = _expand_synonyms(corrected)
    all_text  = " ".join(variants)

    collection = get_collection()

    guaranteed_ids, keyword_sources = _exact_keyword_scan(query, allowed=allowed)

    # ── Vector search ────────────────────────────────────────────────────────
    vector_ranks, vector_chunks = {}, {}
    query_vec  = embed_text(corrected)
    vec_kwargs = {
        "query_embeddings": [query_vec],
        "n_results":        min(config.VECTOR_CANDIDATES, collection.count()),
        "include":          ["documents", "metadatas", "distances"],
    }
    where = _acl_where(allowed, filters)
    if where:
        vec_kwargs["where"] = where
    vres = collection.query(**vec_kwargs)
    for rank, (cid, doc, meta, dist) in enumerate(zip(
        vres["ids"][0], vres["documents"][0],
        vres["metadatas"][0], vres["distances"][0]
    )):
        vector_ranks[cid]  = rank
        vector_chunks[cid] = {
            "id": cid, "text": doc, "meta": meta,
            "vector_score": round(1 - dist, 4),
        }

    # ── BM25 search ─────────────────────────────────────────────────────────
    bm25_ranks, bm25_chunks = {}, {}
    if _bm25 is None:
        _build_bm25()
    if _bm25 is not None:
        try:
            from rapidfuzz import fuzz
            tokens     = all_text.split()
            bm25_raw   = _bm25.get_scores(tokens)
            scored = [
                (idx, score) for idx, score in enumerate(bm25_raw)
                if score > 0 and _is_allowed(_bm25_corpus[idx][2], allowed)
            ]
            ranked_bm25 = sorted(scored, key=lambda x: x[1], reverse=True)[:config.BM25_CANDIDATES]
            for rank, (idx, score) in enumerate(ranked_bm25):
                norm_text, orig_text, meta, cid = _bm25_corpus[idx]
                # fuzzy bonus is computed on normalised text (same space as BM25 scoring)
                fuzzy_bonus = max(
                    (fuzz.partial_ratio(v, norm_text) / 100.0
                     for v in variants
                     if fuzz.partial_ratio(v, norm_text) / 100.0 > 0.75),
                    default=0.0,
                )
                bm25_ranks[cid]  = rank
                bm25_chunks[cid] = {
                    # use original text so UI snippets are readable
                    "id": cid, "text": orig_text, "meta": meta,
                    "bm25_score": round(score + fuzzy_bonus, 4),
                }
        except Exception:
            pass

    # ── Metadata search ──────────────────────────────────────────────────────
    meta_scores = _metadata_search(corrected, top_n=30, allowed=allowed)
    meta_ranks  = {cid: rank for rank, cid in enumerate(meta_scores)}

    # ── RRF merge ────────────────────────────────────────────────────────────
    merged     = _rrf([vector_ranks, bm25_ranks, meta_ranks])
    all_chunks = {**bm25_chunks, **vector_chunks}

    # ── Inject guaranteed exact-match IDs ────────────────────────────────────
    # Any chunk containing the exact keyword that wasn't surfaced by BM25 /
    # vector / metadata search gets a floor RRF score so the reranker can
    # see it and sort it correctly.  We fetch its text from the collection
    # only for IDs not already in all_chunks (minimises extra DB calls).
    missing_ids = guaranteed_ids - set(all_chunks.keys())
    if missing_ids:
        fetched = collection.get(
            ids=list(missing_ids),
            include=["documents", "metadatas"]
        )
        for cid, doc, meta in zip(fetched["ids"], fetched["documents"], fetched["metadatas"]):
            all_chunks[cid] = {"id": cid, "text": doc, "meta": meta,
                               "vector_score": 0, "bm25_score": 0}
    # Give every guaranteed chunk a floor score so it survives into reranking
    for cid in guaranteed_ids:
        if cid not in merged:
            merged[cid] = 0.001  # below any real RRF score but not zero

    chunks = []
    for cid in sorted(merged, key=merged.get, reverse=True):
        if cid not in all_chunks:
            continue
        c    = all_chunks[cid]
        meta = c["meta"]
        if not _is_allowed(meta, allowed):
            continue
        chunks.append({
            "id":           cid,
            "text":         c["text"],
            "score":        round(merged[cid], 6),
            "vector_score": c.get("vector_score", 0),
            "bm25_score":   c.get("bm25_score", 0),
            "source":       meta.get("source", "unknown"),
            # Needed so a frontend can request the file by doc_id (checked
            # against allowed_doc_ids again server-side) instead of a raw
            # file_path — never trust a client-supplied filesystem path.
            "doc_id":       chunk_doc_id(meta),
            "file_path":    meta.get("file_path") or meta.get("filepath", ""),
            "source_type":  meta.get("source_type", ""),
            "doc_type":     meta.get("doc_type", ""),
            "department":   meta.get("department", ""),
            "project":      meta.get("project", ""),
            "date":         meta.get("date", ""),
            "summary":      meta.get("summary", ""),
            "page":         meta.get("page", ""),
            "ocr":          meta.get("ocr", "false"),
        })

    # ── Rerank ───────────────────────────────────────────────────────────────
    if use_reranker and chunks:
        chunks = _rerank(query, chunks, top_n=40)

    # ── Group by document ─────────────────────────────────────────────────────
    doc_map = {}
    for c in chunks:
        src = c["source"]
        if src not in doc_map:
            doc_map[src] = {
                "source":         src,
                "doc_id":         c.get("doc_id", ""),
                "file_path":      c.get("file_path", ""),
                "doc_type":       c.get("doc_type", ""),
                "department":     c.get("department", ""),
                "date":           c.get("date", ""),
                "source_type":    c.get("source_type", ""),
                "summary":        c.get("summary", ""),
                "best_score":     c.get("rerank_score", c["score"]),
                "matched_chunks": [],
            }
        doc_map[src]["matched_chunks"].append({
            "text":         c["text"][:400],
            "page":         c.get("page", ""),
            "score":        c.get("rerank_score", c["score"]),
            "vector_score": c.get("vector_score", 0),
            "bm25_score":   c.get("bm25_score", 0),
            "ocr":          c.get("ocr", "false"),
        })
        best = c.get("rerank_score", c["score"])
        if best > doc_map[src]["best_score"]:
            doc_map[src]["best_score"] = best

    docs = sorted(doc_map.values(), key=lambda x: x["best_score"], reverse=True)

    # Cap at top_n_docs by rank only.
    # A score-based threshold was previously used here but it caused relevant
    # documents to be silently dropped whenever the query phrase is common
    # boilerplate (e.g. "allow to cool naturally" appears in 20+ manuals —
    # every document scores similarly so the tail cut removed real results).
    docs = docs[:top_n_docs]

    # File size — stat'd only for the docs actually returned (not the full
    # doc_map), same "only pay for what's shown" reasoning as the top_n_docs
    # cap itself. Missing/unreadable files degrade to "" rather than raising,
    # since a stale file_path shouldn't break the whole search response.
    for d in docs:
        d["file_size"] = ""
        if d.get("file_path") and os.path.exists(d["file_path"]):
            try:
                d["file_size"] = os.path.getsize(d["file_path"])
            except OSError:
                pass

    # Assign relevance % based on RANK POSITION not raw score.
    #
    # WHY: Two different scoring scales feed into results —
    #   - reranker logits (typically -5 to +10, sigmoid-mapped)
    #   - RRF scores (always 0–0.05, linearly mapped)
    # These scales are incompatible, so a rank-#1 document with a
    # reranker score could display 43% while a rank-#2 keyword-match
    # document displays 77% — confusing and misleading.
    #
    # Rank-based %: rank 1 → 99%, rank 2 → 94%, ..., rank 20 → 4%
    # The displayed % now ALWAYS matches the visual order.
    # Users can trust that a higher % means a higher-ranked result.
    total = len(docs)
    for rank, d in enumerate(docs):
        if total == 1:
            pct = 99
        else:
            # Linear scale: top doc gets 99%, last gets max(4%, ...)
            pct = int(99 - (rank / max(total - 1, 1)) * 95)
        d["relevance_pct"] = max(1, min(99, pct))

    # ── Coverage stats ───────────────────────────────────────────────────────
    coverage = {
        "keyword_file_count":  len(keyword_sources),
        "keyword_chunk_count": sum(keyword_sources.values()),
        "keyword_sources":     keyword_sources,   # {source: chunk_count}
    }

    return docs, coverage


# ─────────────────────────────────────────────────────────────────────────────
# ORIGINAL retrieve()  ← kept exactly as-is, used by stream_answer() in app.py
# ─────────────────────────────────────────────────────────────────────────────
def retrieve(query, filters=None, top_k=None, allowed_doc_ids=None):
    """
    Embed the query and find the top_k most semantically similar chunks.
    allowed_doc_ids: set of doc_ids or None for no filter.
    """
    if top_k is None:
        top_k = config.TOP_K_RESULTS

    allowed = allowed_doc_ids
    if allowed is not None and not allowed:
        return []

    collection = get_collection()
    query_embedding = embed_text(query)

    kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"]
    }
    where = _acl_where(allowed, filters)
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0]
    ):
        if not _is_allowed(meta, allowed):
            continue
        chunks.append({
            "text":        doc,
            "source":      meta.get("source", "unknown"),
            "source_type": meta.get("source_type", ""),
            "page":        meta.get("page", ""),
            "department":  meta.get("department", ""),
            "project":     meta.get("project", ""),
            "doc_type":    meta.get("doc_type", ""),
            "date":        meta.get("date", ""),
            "score":       round(1 - dist, 3)
        })
    return chunks


if __name__ == "__main__":
    queries = [
        "What is the leave policy for interns?",
        "Show me the 2025 audit approval.",
        "What is the status of Project Aurora?",
        "Summarise all documents related to Presstek.",
        "What is recipe for Samosa ?"
    ]
    for q in queries:
        print(f"\n{'='*60}")
        print(f"Query: {q}")
        print('='*60)
        docs, coverage = retrieve_documents(q, top_n_docs=20, use_reranker=False)
        print(f"  Coverage: keyword in {coverage['keyword_file_count']} files / {coverage['keyword_chunk_count']} chunks")
        for i, d in enumerate(docs, 1):
            print(f"  [{i}] {d['relevance_pct']}% | {d['source']}")
        print("---")
        chunks = retrieve(q)
        for i, c in enumerate(chunks, 1):
            print(f"  [{i}] score={c['score']} | {c['source_type']} | {c['source']}")
            print(f"       {c['text'][:120].strip()}...")