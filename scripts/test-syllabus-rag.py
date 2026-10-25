"""Verify live localhost RAG answers against the actual indexed syllabus PDF.

No indexing, model stubs, simulated approvals or cloud access. Saves answer/citation
evidence inside the configured PRIMUS root. Exit zero only when every check passes.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "vendor"), str(PROJECT / "src")]
from pypdf import PdfReader
from primus.config import load_config
from primus.dashboard import api_request
from primus.knowledge import Knowledge
from primus.security import guard_path


CASES = [
    ("What is the course code and how many credits does it carry?",
     [(r"\bIMAST0303\b", r"\bIMAST0303\b"), (r"\b(?:4|four)\b", r"credits\s*:\s*4\b")]),
    ("Which three agent evaluation benchmarks are named in the syllabus?",
     [(r"agent\s*bench", r"agent\s*bench"), (r"\bGAIA\b", r"\bGAIA\b"), (r"web\s*arena", r"web\s*arena")]),
    ("Which tool is named for observability in the syllabus?",
     [(r"\bLangSmith\b", r"observability\s+with\s+LangSmith")]),
    ("What GPU hardware is prescribed for running PRIMUS?", []),
]


def normalized(text):
    return " ".join(text.split())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    config = load_config(PROJECT / "config.json")
    source = guard_path(str(args.source), config)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    documents = api_request(config, "/knowledge")["documents"]
    indexed = next((row for row in documents if row["path"] == str(source) and row["sha256"] == digest and row["current"]), None)
    if not indexed:
        raise ValueError("This exact PDF must already be approved and indexed before testing")
    pages = [page.extract_text().replace("\r\n", "\n").strip() for page in PdfReader(source).pages]
    knowledge = Knowledge(config)
    report = {"source": str(source), "sha256": digest, "document_id": indexed["id"],
              "pages": len(pages), "chunks": indexed["chunk_count"], "model": config["model"]["name"],
              "started_utc": datetime.now(timezone.utc).isoformat(), "tests": [],
              "scope": "Actual authenticated localhost API and configured local model; expected facts manually selected from the supplied PDF. Checks validate these facts and cited passage/page provenance, not general answer correctness."}
    for question, patterns in CASES:
        started = time.monotonic()
        result = api_request(config, "/knowledge/ask", {"question": question}, timeout=180)
        text, sources = result["answer"], result["sources"]
        cited = set(re.findall(r"\[K:[^\]\s]+\]", text))
        provenance, evidence = True, []
        for ref in sources:
            match = re.fullmatch(r"\[K:([0-9a-f]{32}):(\d+)\]", ref["citation"])
            if not match or match[1] != indexed["id"]:
                provenance = False
                continue
            passage = knowledge.read(match[1], int(match[2]))
            page = passage["page"]
            valid = (ref["path"] == str(source) and ref["sha256"] == digest and ref["page"] == page
                     and isinstance(page, int) and 1 <= page <= len(pages)
                     and passage["content"] in pages[page - 1])
            provenance = bool(provenance and valid)
            evidence.append({**ref, "passage_matches_actual_pdf_page": valid,
                             "text": passage["content"]})
        answer_facts = all(re.search(a, normalized(text), re.I) for a, _ in patterns)
        cited_support = all(any(re.search(s, normalized(p["text"]), re.I) for p in evidence) for _, s in patterns)
        labels_match = cited == {p["citation"] for p in sources}
        if patterns:
            passed = bool(sources and answer_facts and cited_support and provenance and labels_match)
        else:
            passed = bool(not sources and not cited and "No matching current passage" in text
                          and not any(re.search(r"\bGPU\b|\bPRIMUS\b", p, re.I) for p in pages))
        row = {"question": question, "answer": text, "sources": sources, "citation_evidence": evidence,
               "expected_fact_patterns": patterns, "answer_contains_expected_facts": bool(answer_facts),
               "expected_facts_supported_by_cited_passages": bool(cited_support),
               "citations_match_source_pages": provenance, "citation_labels_match_source_list": labels_match,
               "negative_control": not bool(patterns), "elapsed_seconds": round(time.monotonic() - started, 2), "passed": passed}
        report["tests"].append(row)
        print(json.dumps({"question": question, "answer": text, "pdf_pages": sorted({p['page'] for p in sources}), "passed": passed}), flush=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, "Source changed during the test"
    report["all_passed"] = all(row["passed"] for row in report["tests"])
    directory = config["_root"] / "outputs" / "knowledge-tests"
    directory.mkdir(exist_ok=True)
    target = directory / ("syllabus-" + uuid.uuid4().hex + ".json")
    with target.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({"report": str(target), "all_passed": report["all_passed"]}), flush=True)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
