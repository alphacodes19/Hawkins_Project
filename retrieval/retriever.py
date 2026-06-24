import chromadb
import config
from pipeline.embedder import embed_text

_collection = None


def get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        _collection = client.get_collection(config.CHROMA_COLLECTION)
    return _collection


def retrieve(query, filters=None, top_k=None):
    """
    Embed the query and find the top_k most semantically similar chunks
    in ChromaDB. Optionally filter by metadata fields (e.g. department).
    Returns a list of dicts with text, source, score, and metadata.
    """
    if top_k is None:
        top_k = config.TOP_K_RESULTS

    collection = get_collection()
    query_embedding = embed_text(query)

    kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"]
    }
    if filters:
        kwargs["where"] = filters

    results = collection.query(**kwargs)

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0]
    ):
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
        chunks = retrieve(q)
        for i, c in enumerate(chunks, 1):
            print(f"  [{i}] score={c['score']} | {c['source_type']} | {c['source']}")
            print(f"       {c['text'][:120].strip()}...")