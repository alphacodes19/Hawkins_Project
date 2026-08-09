"""
api/routers/chat.py — streamed answer generation
====================================================
Port of app.py's stream_answer(). The only structural change is *how* it
streams: Streamlit's st.write_stream() pulled tokens from a Python generator
inside the same process/script rerun. Here the same generator logic feeds a
Server-Sent-Events response so the browser can render tokens as they arrive
without polling or WebSocket infrastructure — SSE is the minimum-complexity
tool for one-directional token streaming.

This is a genuine `async def` generator using ollama.AsyncClient, not a sync
generator run through Starlette's threadpool wrapper. I verified directly
(a live server test, not just reading the code) that the sync-generator
version was NOT silently buffering — Starlette streams a sync generator's
yields incrementally, each `next()` call handed to the threadpool
individually, not all at once at the end. So this change isn't fixing a
"streaming is broken" bug; it's fixing a real but different problem: a sync
generator doing long-running I/O occupies one threadpool worker for the
entire duration of a generation. Starlette's default threadpool is a fixed
size — several concurrent long generations could exhaust it and start
queueing/blocking *unrelated* sync endpoints elsewhere in the app. An async
generator releases the event loop between awaits instead of pinning a
worker thread, which is the actual scalability fix here.

Each SSE frame is `data: <json>\n\n`. Frame shapes:
  {"type": "token", "text": "..."}      — one generated token/piece
  {"type": "done", "answer": "...", "chunks": [...], "faithfulness": {...}|null}
  {"type": "error", "message": "..."}
"""

import json
import logging
import time

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


async def _generate(question: str, docs: list, allowed):
    import ollama
    from retrieval.generator import ANSWER_PROMPT
    from eval.faithfulness_check import check_faithfulness

    # Wraps the ENTIRE body, not just the ollama.chat() call — see the
    # docstring above; a mid-generator failure that never yields anything is
    # indistinguishable from "nothing happened" in the UI otherwise.
    try:
        t_start = time.monotonic()
        # _build_chunks and retrieve() below are still sync/CPU-bound (BM25,
        # numpy sorting, no real I/O) — genuinely fast, not worth an
        # asyncio.to_thread wrapper for microsecond-scale work. Only the
        # ollama call, which is real network I/O with multi-second-plus
        # latency, needed to become actually async.
        chunks = _build_chunks(question, docs, allowed)
        t_chunks = time.monotonic()

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
        prompt_chars = len(prompt)
        t_prompt = time.monotonic()

        full_answer = []
        t_first_token = None
        try:
            client = ollama.AsyncClient()
            stream = await client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={
                    "num_ctx": config.OLLAMA_NUM_CTX,
                    "num_predict": 512,
                    "temperature": 0.1,
                },
                stream=True,
            )
            async for part in stream:
                token = part["message"]["content"]
                if token:
                    if t_first_token is None:
                        t_first_token = time.monotonic()
                        # This is the number that actually matters for "why is
                        # the answer slow" — everything before it (retrieval,
                        # chunk selection, prompt building) is normally
                        # milliseconds. If time-to-first-token is itself tens
                        # of seconds, that's the local model (currently
                        # config.OLLAMA_MODEL = "qwen2.5:14b", a genuinely
                        # large model) processing the prompt — a
                        # hardware/model-size cost, not something fixable in
                        # this code. Logged unconditionally, not just on slow
                        # requests, so there's a baseline to compare against
                        # rather than only ever seeing outliers.
                        logger.info(
                            "chat timing: retrieval=%.2fs prompt_build=%.2fs "
                            "TIME_TO_FIRST_TOKEN=%.2fs (prompt=%d chars, model=%s, num_ctx=%d)",
                            t_chunks - t_start, t_prompt - t_chunks,
                            t_first_token - t_prompt, prompt_chars,
                            config.OLLAMA_MODEL, config.OLLAMA_NUM_CTX,
                        )
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
        t_done = time.monotonic()
        logger.info(
            "chat timing: total=%.2fs (retrieval=%.2fs, first_token=%.2fs, generation=%.2fs, %d chars generated)",
            t_done - t_start, t_chunks - t_start,
            (t_first_token - t_prompt) if t_first_token else 0,
            t_done - (t_first_token or t_prompt), len(answer),
        )
        try:
            faithfulness = check_faithfulness(answer=answer, chunks=chunks, run_llm_check=False)
        except Exception:
            faithfulness = None

        yield _sse({"type": "done", "answer": answer, "chunks": chunks, "faithfulness": faithfulness})

    except Exception as e:
        logger.exception("Unexpected failure before generation started")
        yield _sse({"type": "error", "message": f"Unexpected error: {e}"})


@router.post("/stream")
async def chat_stream(body: ChatRequest, user: dict = Depends(get_current_user)):
    allowed = authdb.allowed_doc_ids(user)
    return StreamingResponse(
        _generate(body.question, body.docs, allowed),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if deployed behind one
        },
    )
