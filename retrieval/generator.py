"""
generator.py — Standalone answer generator (DEPRECATED / SUPERSEDED)
======================================================================
STATUS: This module is NO LONGER the primary answer-generation path.

app.py generates answers inline via stream_answer(), which:
  - Uses retrieve_documents() (full hybrid BM25+dense+rerank pipeline) as the
    primary retrieval path, falling back to retrieve() only if docs is None.
  - Reads context-window size from config.OLLAMA_NUM_CTX (currently 16 384),
    NOT the hardcoded 8 192 that this file previously contained.

WHY KEPT: The ask() convenience function and __main__ demo block are useful for
one-off CLI testing and as documentation of the intended API surface.

DO NOT call generate_answer() or ask() from app.py.  Use stream_answer() there.

If you resurrect this module, make sure to:
  1. Switch retrieval to retrieve_documents() (hybrid pipeline).
  2. Verify config.OLLAMA_NUM_CTX is honoured (already fixed below).
"""

import ollama
import config

ANSWER_PROMPT = """You are a concise enterprise knowledge assistant for Hawkins Cookers Limited.
Answer the question using ONLY the context below. Be brief and direct — 3 to 5 sentences maximum.
Cite the source in brackets like [source_name] for each fact.
If the answer is not in the context, say: "I could not find this information in the available documents."
Never make up information.

CONTEXT:
{context}

QUESTION: {question}

ANSWER (be concise):"""


def generate_answer(question, chunks):
    """
    Build a context string from retrieved chunks and send to the local LLM.
    Returns the answer text plus source attribution info.

    NOTE: This uses the plain vector-only retrieve() path via ask().  For better
    quality, prefer the hybrid retrieve_documents() path in app.py's stream_answer().
    """
    if not chunks:
        return {
            "answer": "I could not find this information in the available documents.",
            "sources": [],
            "chunks_used": 0
        }

    context_parts = []
    for c in chunks:
        source_label = c["source"]
        if c.get("page"):
            source_label += f" | page {c['page']}"
        if c.get("source_type"):
            source_label += f" | {c['source_type']}"
        context_parts.append(f"[{source_label}]\n{c['text']}")

    context = "\n\n".join(context_parts)

    response = ollama.chat(
        model=config.OLLAMA_MODEL,
        messages=[{
            "role": "user",
            "content": ANSWER_PROMPT.format(context=context, question=question)
        }],
        options={"num_ctx": config.OLLAMA_NUM_CTX}  # was hardcoded 8192; now stays in sync with config
    )

    return {
        "answer":      response["message"]["content"].strip(),
        "sources":     list(dict.fromkeys(c["source"] for c in chunks)),  # ordered unique
        "chunks_used": len(chunks)
    }


def ask(question, filters=None, top_k=None, allowed_doc_ids=None):
    """
    Convenience function: retrieve + generate in one call.
    Use this in app.py and tests.

    allowed_doc_ids defaults to None, which means UNRESTRICTED (admin-level)
    access. That default is correct for the __main__ block below and for tests,
    but any caller serving a real user MUST pass the user's permitted set —
    otherwise this function happily answers from documents that user cannot see.
    """
    from retrieval.retriever import retrieve
    chunks = retrieve(question, filters=filters, top_k=top_k,
                      allowed_doc_ids=allowed_doc_ids)
    result = generate_answer(question, chunks)
    result["chunks"] = chunks   # attach for source display in UI
    return result


if __name__ == "__main__":
    demo_questions = [
        "What is the leave policy for interns?",
        "Show me the 2025 audit approval.",
        "What is the status of Project Aurora?",
        "Summarise all documents related to Presstek.",
    ]
    for q in demo_questions:
        print(f"\n{'='*60}")
        print(f"Q: {q}")
        print('='*60)
        result = ask(q)
        print(f"\nA: {result['answer']}")
        print(f"\nSources ({result['chunks_used']} chunks): {result['sources']}")