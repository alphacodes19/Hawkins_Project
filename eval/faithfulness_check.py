#!/usr/bin/env python3
"""
eval/faithfulness_check.py — Answer Faithfulness Checker
==========================================================
Checks whether the generated answer is grounded in the retrieved context,
addressing the hallucination risk in the RAG pipeline.

THREE LEVELS OF CHECKING (fastest to most thorough)
----------------------------------------------------
Level 1 — Citation audit (always runs, zero ML cost)
  Every [source_name] cited in the answer must appear in the retrieved
  context. Phantom citations — where the model names a document it was
  never given — are caught here.

Level 2 — N-gram overlap (fast, no ML)
  Extracts factual claims (sentences with numbers, names, dates) from the
  answer and checks whether distinctive n-grams from each claim appear in
  at least one retrieved chunk. Low overlap → possible hallucination.

Level 3 — LLM self-check (optional, requires Ollama running)
  Sends a structured faithfulness prompt to the LLM asking it to rate
  each sentence in the answer as: SUPPORTED / UNSUPPORTED / NOT_IN_CONTEXT.
  This is the most accurate but also the slowest check.

HOW TO USE — standalone
-----------------------
  python eval/faithfulness_check.py \
      --answer "The leave policy allows 15 days [HR_Policy_2024.pdf]." \
      --context "HR_Policy_2024.pdf: Employees are entitled to 15 days of leave."

HOW TO USE — in app.py (call check_faithfulness() directly)
-------------------------------------------
  from eval.faithfulness_check import check_faithfulness

  result = check_faithfulness(answer=answer_text, chunks=retrieved_chunks)
  if not result["is_faithful"]:
      st.warning(f"Answer may contain unsupported claims: {result['issues']}")

RETURN VALUE
------------
  {
    "is_faithful":   bool,        # True if all checks pass
    "score":         float,       # 0.0–1.0 overall faithfulness estimate
    "citation_ok":   bool,        # All cited sources exist in context
    "phantom_cites": list[str],   # Sources cited but not in context
    "overlap_score": float,       # N-gram overlap of claims vs context
    "low_overlap_claims": list,   # Specific sentences with low support
    "llm_check":     dict | None, # Level-3 result if requested
    "issues":        list[str],   # Human-readable issue descriptions
  }
"""

import argparse
import os
import re
import sys
from typing import Optional

# ── Project root on path ──────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Level 1 — Citation audit
# ─────────────────────────────────────────────────────────────────────────────

def _extract_citations(answer: str) -> list[str]:
    """Extract all [source_name] citations from the answer."""
    return re.findall(r"\[([^\[\]]+?)\]", answer)


def _check_citations(answer: str, chunks: list[dict]) -> dict:
    """
    Verify every cited source exists in the retrieved chunks.
    Returns phantom_cites (cited but not provided) and missing_cites
    (in context but not cited — informational, not an error).
    """
    cited    = set(_extract_citations(answer))
    provided = {c.get("source", "") for c in chunks}
    provided |= {os.path.basename(s) for s in provided}  # also check basenames

    phantom = []
    for cite in cited:
        # Accept if the citation matches a full source name or its basename
        full_match = cite in provided
        base_match = any(cite in os.path.basename(p) or os.path.basename(p) in cite
                         for p in provided)
        if not full_match and not base_match:
            phantom.append(cite)

    return {
        "cited":         sorted(cited),
        "provided":      sorted(provided - {""}),
        "phantom_cites": phantom,
        "citation_ok":   len(phantom) == 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Level 2 — N-gram overlap
# ─────────────────────────────────────────────────────────────────────────────

def _tokenise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _ngrams(tokens: list[str], n: int) -> set[tuple]:
    return {tuple(tokens[i:i+n]) for i in range(len(tokens) - n + 1)}


def _is_factual_sentence(sentence: str) -> bool:
    """Heuristic: sentences with numbers, dates, names are most likely to hallucinate."""
    has_number  = bool(re.search(r"\d", sentence))
    has_caps    = bool(re.search(r"\b[A-Z][a-z]{2,}", sentence))   # proper noun
    longer_than = len(sentence.split()) >= 5
    return longer_than and (has_number or has_caps)


def _ngram_overlap(answer: str, chunks: list[dict], n: int = 3) -> dict:
    """
    For each factual sentence in the answer, checks whether its FACTUAL
    ANCHORS — numbers, dates, proper nouns, and alphanumeric codes — appear
    somewhere in the retrieved context, rather than requiring the sentence's
    exact word sequences (trigrams) to match the source's phrasing.

    This used to be pure trigram overlap: what fraction of the sentence's
    3-word windows appear verbatim in the context. That's the wrong thing to
    measure for a RAG answer, because a good LLM is SUPPOSED to paraphrase
    and synthesize rather than copy — reordering a clause or swapping one
    word breaks up to 3 overlapping trigrams around it. A factually perfect,
    properly-cited paraphrase like "The Dandi March led by Gandhi began at
    Sabarmati Ashram..." scores ~3% trigram overlap against a source phrased
    as "Mahatma Gandhi began the Dandi March from Sabarmati Ashram..." even
    though every fact in it is correct and sourced — verified directly
    against this exact wording before shipping this change.

    Anchors are what actually matters for catching hallucination: a
    fabricated date, number, or name wouldn't appear in the context at all,
    regardless of how the surrounding prose is worded. Checking those
    specifically is far more forgiving of legitimate paraphrasing while
    staying just as sensitive to genuine fabrication — arguably more
    sensitive, since a hallucinated number can no longer hide behind
    otherwise-high overlap from correctly-copied surrounding words.
    """
    context_text = " ".join(c.get("text", "") for c in chunks)
    context_lower = context_text.lower()
    # Trigram set kept for factual_sentences/back-compat scoring path below;
    # anchor matching (does the number/name appear at all) is now what
    # actually drives is_faithful, not this.
    context_ngrams = _ngrams(_tokenise(context_text), n)

    sentences = re.split(r"(?<=[.!?])\s+", answer.strip())
    factual   = [s for s in sentences if _is_factual_sentence(s)]

    low_overlap = []
    scores      = []

    for sent in factual:
        anchors = _extract_factual_anchors(sent)
        if not anchors:
            # Had numbers/caps per _is_factual_sentence's cheaper regex check,
            # but nothing survived as a standalone anchor (e.g. sentence-
            # initial capitalisation only) — nothing concrete to verify,
            # so don't penalise it.
            continue

        found = [a for a in anchors if a.lower() in context_lower]
        overlap = len(found) / len(anchors)
        scores.append(overlap)
        if overlap < 0.6:   # most of this sentence's concrete facts should be traceable to context
            low_overlap.append({
                "sentence": sent.strip(),
                "overlap":  round(overlap, 2),
                "unverified_anchors": [a for a in anchors if a not in found],
            })

    overall = sum(scores) / len(scores) if scores else 1.0  # no checkable anchors → assume ok

    return {
        "overlap_score":      round(overall, 3),
        "factual_sentences":  len(factual),
        "low_overlap_claims": low_overlap,
        "overlap_ok":         overall >= 0.6 and len(low_overlap) == 0,
    }


def _extract_factual_anchors(sentence: str) -> list[str]:
    """
    The parts of a sentence that would be concretely WRONG if hallucinated —
    as opposed to prose phrasing, which naturally varies with paraphrasing
    even when the underlying claim is completely faithful to the source.
    """
    anchors: list[str] = []
    stripped_sentence = sentence.strip()

    # Numbers: dates, quantities, percentages, decimals, codes like "SKU 4177"
    anchors += re.findall(r"\d[\d,.:%]*", sentence)

    # Capitalized-word runs. A SINGLE capitalized word at the very start of
    # the sentence is NOT kept as an anchor here — English capitalizes the
    # first word of every sentence regardless of part of speech, so "Emails
    # at Hawkins Cookers Limited will..." capitalizes "Emails" for the same
    # reason it would capitalize "The" or "Under" — sentence position, not
    # properness. A fixed stopword list ("the", "this", "that"...) can never
    # cover this fully, because ANY ordinary word can end up sentence-
    # initial ("Emails", "Views" — both genuinely tripped this in practice,
    # not hypothetically). A RUN of 2+ consecutive capitalized words
    # ("Hawkins Cookers Limited") stays a reliable anchor even at sentence
    # start, since two-plus ordinary words coincidentally capitalized in a
    # row is not something normal English does. A single capitalized word
    # is trusted as an anchor once it's NOT sentence-initial, since mid-
    # sentence capitalization is a real signal English reserves for proper
    # nouns.
    for match in re.finditer(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,}){0,3}\b", sentence):
        text = match.group()
        is_multi_word = " " in text
        is_sentence_initial = stripped_sentence.startswith(text)
        if is_multi_word or not is_sentence_initial:
            anchors.append(text)

    # Alphanumeric codes / model numbers / product codes (e.g. "ISET1", "P001")
    anchors += re.findall(r"\b[A-Za-z]+\d+[A-Za-z0-9]*\b", sentence)

    # De-dupe, drop trivially short matches — anything under 3 chars isn't a
    # meaningful standalone fact to verify.
    seen = set()
    out = []
    for a in anchors:
        a = a.strip()
        if len(a) < 3 or a.lower() in seen:
            continue
        seen.add(a.lower())
        out.append(a)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Level 3 — LLM self-check (optional)
# ─────────────────────────────────────────────────────────────────────────────

_LLM_FAITHFULNESS_PROMPT = """\
You are a faithfulness auditor for a RAG system.

RETRIEVED CONTEXT (the only documents the AI was given):
{context}

GENERATED ANSWER:
{answer}

For each sentence in the GENERATED ANSWER, classify it as:
- SUPPORTED   — directly supported by a sentence in the context
- PARAPHRASE  — a reasonable paraphrase of context content
- UNSUPPORTED — makes a claim not present in the context
- META        — meta-commentary ("I found...", "Based on the documents...")

Reply ONLY with a JSON array, no other text. Example:
[
  {{"sentence": "...", "label": "SUPPORTED"}},
  {{"sentence": "...", "label": "UNSUPPORTED"}}
]
"""


def _llm_faithfulness_check(answer: str, chunks: list[dict]) -> Optional[dict]:
    """
    Optional Level-3 check using the local Ollama LLM.
    Returns None if Ollama is unavailable or returns malformed output.
    """
    try:
        import ollama
        import config
        import json as _json

        context_text = "\n\n".join(
            f"[{c.get('source', 'unknown')}]\n{c.get('text', '')}"
            for c in chunks[:6]   # cap context to avoid overwhelming the check
        )

        prompt = _LLM_FAITHFULNESS_PROMPT.format(
            context=context_text[:6000],  # ~1500 tokens
            answer=answer,
        )

        response = ollama.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 8192, "temperature": 0},
        )
        raw = response["message"]["content"].strip()

        # Strip markdown fences if present
        raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)

        sentences = _json.loads(raw)
        unsupported = [s for s in sentences if s.get("label") == "UNSUPPORTED"]

        return {
            "sentences":         sentences,
            "unsupported_count": len(unsupported),
            "unsupported":       unsupported,
            "llm_ok":            len(unsupported) == 0,
        }

    except Exception as e:
        return {"error": str(e), "llm_ok": None}


# ─────────────────────────────────────────────────────────────────────────────
# Main public API
# ─────────────────────────────────────────────────────────────────────────────

def check_faithfulness(
    answer:      str,
    chunks:      list[dict],
    run_llm_check: bool = False,
) -> dict:
    """
    Run all faithfulness checks on a generated answer.

    Parameters
    ----------
    answer        : The text generated by stream_answer() / generate_answer()
    chunks        : The retrieved chunks that were fed as context (list of dicts
                    with at least 'source' and 'text' keys)
    run_llm_check : Whether to run the slower LLM self-check (Level 3)

    Returns
    -------
    dict with keys: is_faithful, score, citation_ok, phantom_cites,
                    overlap_score, low_overlap_claims, llm_check, issues
    """
    issues = []

    # Level 1 — Citation audit
    cit = _check_citations(answer, chunks)
    if not cit["citation_ok"]:
        issues.append(
            f"Phantom citations (cited but not in context): {cit['phantom_cites']}"
        )

    # Level 2 — N-gram overlap
    olap = _ngram_overlap(answer, chunks)
    if not olap["overlap_ok"]:
        if olap["overlap_score"] < 0.3:
            issues.append(
                f"Low overall n-gram overlap with context "
                f"({olap['overlap_score']:.0%} — possible hallucination)"
            )
        for claim in olap["low_overlap_claims"]:
            issues.append(
                f"Low-support sentence ({claim['overlap']:.0%} overlap): "
                f"\"{claim['sentence'][:80]}...\""
            )

    # Level 3 — Optional LLM self-check
    llm_result = None
    if run_llm_check:
        llm_result = _llm_faithfulness_check(answer, chunks)
        if llm_result and llm_result.get("llm_ok") is False:
            for u in llm_result.get("unsupported", []):
                issues.append(f"LLM flagged as UNSUPPORTED: \"{u['sentence'][:80]}\"")

    # Overall faithfulness score (simple weighted average)
    citation_score = 1.0 if cit["citation_ok"] else 0.0
    # No aggressive rescaling needed anymore — anchor-based overlap_score is
    # already a meaningful 0-1 confidence value (fraction of concrete facts
    # traceable to context), unlike the old trigram score where even a
    # faithful paraphrase rarely exceeded ~10-20%, which is why that version
    # applied a 2x boost (dividing by 0.5) just to keep faithful answers out
    # of the gutter. Applying that same boost here would make an answer
    # with only 50% of its facts verifiable read as a perfect score.
    overlap_score  = olap["overlap_score"]
    llm_score      = 1.0  # neutral if not run
    if llm_result and llm_result.get("sentences"):
        n = len(llm_result["sentences"])
        n_bad = llm_result.get("unsupported_count", 0)
        llm_score = max(0.0, 1.0 - n_bad / n) if n > 0 else 1.0

    if run_llm_check and llm_result and llm_result.get("sentences"):
        score = 0.4 * citation_score + 0.3 * overlap_score + 0.3 * llm_score
    else:
        score = 0.5 * citation_score + 0.5 * overlap_score

    return {
        "is_faithful":        len(issues) == 0,
        "score":              round(score, 3),
        "citation_ok":        cit["citation_ok"],
        "phantom_cites":      cit["phantom_cites"],
        "cited":              cit["cited"],
        "provided_sources":   cit["provided"],
        "overlap_score":      olap["overlap_score"],
        "factual_sentences":  olap["factual_sentences"],
        "low_overlap_claims": olap["low_overlap_claims"],
        "llm_check":          llm_result,
        "issues":             issues,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Check answer faithfulness against retrieved context"
    )
    parser.add_argument("--answer",  required=True, help="Generated answer text")
    parser.add_argument("--context", required=True,
                        help="Context string (format: 'source: text' blocks separated by \\n\\n)")
    parser.add_argument("--llm",  action="store_true",
                        help="Run optional LLM self-check (requires Ollama)")
    args = parser.parse_args()

    # Parse simple context format: "source: text\n\nsource2: text2"
    chunks = []
    for block in args.context.split("\n\n"):
        if ":" in block:
            source, _, text = block.partition(":")
            chunks.append({"source": source.strip(), "text": text.strip()})
        else:
            chunks.append({"source": "unknown", "text": block.strip()})

    result = check_faithfulness(args.answer, chunks, run_llm_check=args.llm)

    print("\n=== Faithfulness Check ===\n")
    print(f"  Overall faithful: {'YES' if result['is_faithful'] else 'NO'}")
    print(f"  Score:            {result['score']:.2f} / 1.00")
    print(f"  Citation audit:   {'PASS' if result['citation_ok'] else 'FAIL'}")
    if result["phantom_cites"]:
        print(f"  Phantom cites:    {result['phantom_cites']}")
    print(f"  N-gram overlap:   {result['overlap_score']:.2f}")
    if result["low_overlap_claims"]:
        print(f"  Low-support sentences:")
        for c in result["low_overlap_claims"]:
            print(f"    [{c['overlap']:.0%}] {c['sentence'][:80]}")
    if result["issues"]:
        print(f"\n  Issues found:")
        for issue in result["issues"]:
            print(f"    • {issue}")
    else:
        print("\n  No issues found — answer appears grounded in context.")

    if result["llm_check"]:
        llm = result["llm_check"]
        if "error" in llm:
            print(f"\n  LLM check failed: {llm['error']}")
        else:
            print(f"\n  LLM check: {llm['unsupported_count']} unsupported sentence(s)")


if __name__ == "__main__":
    main()
