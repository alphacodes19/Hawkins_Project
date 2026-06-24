from sentence_transformers import SentenceTransformer
import config

# Load the embedding model once when this module is imported.
# BGE-M3 turns text into a 1024-dimension vector that captures its meaning.
_model = None


def get_model():
    """Lazy-load the model so it's only loaded once, not every function call."""
    global _model
    if _model is None:
        print(f"Loading embedding model: {config.EMBEDDING_MODEL} (first time only, may take a minute)...")
        _model = SentenceTransformer(config.EMBEDDING_MODEL)
    return _model


def embed_text(text):
    """
    Converts a piece of text into a dense vector (list of floats).
    This vector captures the MEANING of the text, so ChromaDB can later
    find semantically similar chunks even if they don't share exact words.
    """
    model = get_model()
    embedding = model.encode(text)
    return embedding.tolist()


# Quick manual test
if __name__ == "__main__":
    sample_text = "Project Aurora is a next-generation pressure cooker with a smart valve."
    print(f"Embedding text: \"{sample_text}\"\n")

    vector = embed_text(sample_text)
    print(f"Vector length: {len(vector)}")
    print(f"First 5 values: {vector[:5]}")

    # Sanity check: two similar sentences should produce similar vectors
    similar_text = "The Aurora project involves a pressure cooker with an intelligent valve."
    different_text = "The HR department updated the intern leave policy."

    v1 = embed_text(sample_text)
    v2 = embed_text(similar_text)
    v3 = embed_text(different_text)

    import numpy as np
    def cosine_sim(a, b):
        a, b = np.array(a), np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    print(f"\nSimilarity (similar sentences): {cosine_sim(v1, v2):.3f}")
    print(f"Similarity (different sentences): {cosine_sim(v1, v3):.3f}")
    print("\n(Similar sentences should score much higher than different ones)")