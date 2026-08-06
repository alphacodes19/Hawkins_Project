"""
api/routers/chat.py — streamed answer generation
====================================================
Port of app.py's stream_answer(). The only structural change is *how* it
streams: Streamlit's st.write_stream() pulled tokens from a Python generator
inside the same process/script rerun. Here the same generator logic feeds a
Server-Sent-Events response so the browser can render tokens as they arrive
without polling or WebSocket infrastructure — SSE is the minimum-complexity
tool for one-directional token streaming.

Each SSE frame is `data: <json>\n\n`. Frame shapes:
  {"type": "token", "text": "..."}      — one generated token/piece
  {"type": "done", "answer": "...", "chunks": [...], "faithfulness": {...}|null}
  {"type": "error", "message": "..."}
"""

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

import config
from auth import db as authdb
from api.deps import get_current_user
from api.schemas import ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger("hawkins.chat")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _build_chunks(question: str, docs: list, allowed):
    """Same chunk-selection logic as app.py's stream_answer()."""
    if docs:
        chunks = []
        for d in docs[: config.ANSWER_TOP_DOCS]:
            sorted_mc = sorted(d["matched_chunks"], key=lambda x: x["score"], reverse=True)
            for mc in sorted_mc[: config.ANSWER_CHUNKS_PER_DOC]:
                chunks.append({
                    "text": mc["text"],
                    "source": d["source"],
                    "source_type": d.get("source_type", ""),
                    "doc_type": d.get("doc_type", ""),
                    "page": mc.get("page", ""),
                    "score": round(mc["score"], 3),
                })
        return chunks

    from retrieval.retriever import retrieve
    return retrieve(question, allowed_doc_ids=allowed)


def _generate(question: str, docs: list, allowed):
    import ollama
    from retrieval.generator import ANSWER_PROMPT
    from eval.faithfulness_check import check_faithfulness

    # Wraps the ENTIRE body, not just the ollama.chat() call. An exception
    # anywhere before that call (chunk-building, prompt formatting) used to
    # kill the generator before it ever yielded a byte — StreamingResponse
    # had already committed a 200 status, so the browser would see a stream
    # that opens and then closes with zero data: no "done", no "error", just
    # silence. That's indistinguishable from "nothing happened" in the UI,
    # which is the single worst failure mode here — catching everything and
    # always yielding at least one SSE frame guarantees the UI can tell the
    # difference between "still working" and "actually failed."
    try:
        chunks = _build_chunks(question, docs, allowed)

        if not chunks:
            yield _sse({"type": "token", "text": "I could not find this information in the available documents."})
            yield _sse({"type": "done", "answer": "I could not find this information in the available documents.",
                         "chunks": [], "faithfulness": None})
            return

        context_parts = []
        for c in chunks:
            label = c["source"]
            if c.get("page"):
                label += f" | page {c['page']}"
            if c.get("source_type"):
                label += f" | {c['source_type']}"
            context_parts.append(f"[{label}]\n{c['text']}")
        context = "\n\n".join(context_parts)

        prompt = ANSWER_PROMPT.format(context=context, question=question)

        full_answer = []
        try:
            stream = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={
                    "num_ctx": config.OLLAMA_NUM_CTX,
                    "num_predict": 512,
                    "temperature": 0.1,
                },
                stream=True,
            )
            for part in stream:
                token = part["message"]["content"]
                if token:
                    full_answer.append(token)
                    yield _sse({"type": "token", "text": token})
        except Exception as e:
            # Most common cause: config.OLLAMA_MODEL (currently
            # "qwen2.5:14b") not matching what's actually pulled locally —
            # check `ollama list`. Also logged server-side, since a failed
            # answer otherwise only shows up as one red line in the browser.
            logger.exception("Answer generation failed for model=%s", config.OLLAMA_MODEL)
            yield _sse({"type": "error", "message": str(e)})
            return

        answer = "".join(full_answer)
        try:
            faithfulness = check_faithfulness(answer=answer, chunks=chunks, run_llm_check=False)
        except Exception:
            faithfulness = None

        yield _sse({"type": "done", "answer": answer, "chunks": chunks, "faithfulness": faithfulness})

    except Exception as e:
        logger.exception("Unexpected failure before generation started")
        yield _sse({"type": "error", "message": f"Unexpected error: {e}"})


@router.post("/stream")
def chat_stream(body: ChatRequest, user: dict = Depends(get_current_user)):
    allowed = authdb.allowed_doc_ids(user)
    return StreamingResponse(
        _generate(body.question, body.docs, allowed),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if deployed behind one
        },
    )
