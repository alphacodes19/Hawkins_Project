import ollama
import config

ANSWER_PROMPT = """You are a helpful enterprise knowledge assistant for Hawkins Cookers Limited.
Answer the question using ONLY the context provided below.
For every fact you state, cite the source document in brackets like [source_name].
If the answer is not in the context, say exactly: "I could not find this information in the available documents."
Never make up information.

CONTEXT:
{context}

QUESTION: {question}

ANSWER:"""


def generate_answer(question, chunks):
    """
    Build a context string from retrieved chunks and send to local LLaMA.
    Returns the answer text plus source attribution info.
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
        options={"num_ctx": 4096}
    )

    return {
        "answer":      response["message"]["content"].strip(),
        "sources":     list(dict.fromkeys(c["source"] for c in chunks)),  # ordered unique
        "chunks_used": len(chunks)
    }


def ask(question, filters=None, top_k=None):
    """
    Convenience function: retrieve + generate in one call.
    Use this in app.py and tests.
    """
    from retrieval.retriever import retrieve
    chunks = retrieve(question, filters=filters, top_k=top_k)
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