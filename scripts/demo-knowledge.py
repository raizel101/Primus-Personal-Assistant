"""Three real local-model RAG demos with an isolated synthetic test corpus.

No personal documents or production memory are indexed. Fixture construction is test
setup, not a claimed human consent event. No model downloads or cloud calls.
"""
from pathlib import Path
import json
import sys
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "vendor"), str(PROJECT / "src")]
from primus.config import load_config
from primus.knowledge import Knowledge, answer


def main():
    original = load_config(PROJECT / "config.json")
    identifier = uuid.uuid4().hex
    workspace = original["_root"] / "temp" / ("knowledge-demo-" + identifier)
    workspace.mkdir()
    (workspace / "logs").mkdir()
    config = {**original, "_root": workspace, "approved_folders": [], "knowledge": {"enabled": True}}
    files = {
        "release.md": "SYNTHETIC TEST DATA: Atlas release codename is Cobalt Lantern. Atlas review meeting is Tuesday at 14:20, using 24-hour local time. These are fictional fixture facts, not facts about the user.\n",
        "storage.md": "SYNTHETIC TEST DATA: Atlas database engine is SQLite. Atlas approved benchmark dataset identifier is iris-violet-17. These are fictional fixture facts, not facts about the user.\n",
    }
    knowledge = Knowledge(config)
    for name, text in files.items():
        path = workspace / name
        path.write_text(text, encoding="utf-8")
        args = {"path": str(path)}
        knowledge.preview(args)
        knowledge.index(args)  # Synthetic fixture, outside production memory.
    questions = [
        ("What is the Atlas release codename?", ["cobalt lantern"]),
        ("What is the Atlas approved benchmark dataset identifier?", ["iris-violet-17"]),
        ("What is the Atlas release codename and which database engine does Atlas use?", ["cobalt lantern", "sqlite"]),
    ]
    demos = []
    for question, expected in questions:
        result = answer(config, question)
        verified = bool(result["sources"] and all(value in result["answer"].casefold() for value in expected))
        demos.append({"question": question, **result, "expected_fixture_terms": expected, "verified": verified})
        print(json.dumps({"question": question, "answer": result["answer"], "source_count": len(result["sources"]), "verified": verified}), flush=True)
    target_dir = original["_root"] / "outputs" / "knowledge-demos"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / (identifier + ".json")
    with target.open("x", encoding="utf-8") as out:
        json.dump({"model": original["model"]["name"], "fixture": str(workspace), "corpus": "Synthetic setup only; production knowledge unchanged; no human approval event claimed", "demos": demos, "all_verified": all(d["verified"] for d in demos)}, out, indent=2)
    print(json.dumps({"report": str(target), "all_verified": all(d["verified"] for d in demos)}), flush=True)
    return 0 if all(d["verified"] for d in demos) else 1


if __name__ == "__main__":
    raise SystemExit(main())
