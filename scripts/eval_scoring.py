"""Scoring benchmark: detect regressions in how Thought → Speak scores answers.

    python scripts/eval_scoring.py                  # built-in scorer vs. the saved baseline (deterministic)
    python scripts/eval_scoring.py --update-baseline  # after an INTENTIONAL scoring change (bump HEURISTIC_VERSION)
    python scripts/eval_scoring.py --live           # also score with the configured AI provider (costs API calls)

Checks, per case group (same prompt, human-ranked strong > medium > weak):
  1. ordering   — higher human tier must score higher overall
  2. framework  — expected parts detected / missing
  3. verdict    — evaluator verdict within the expected band
  4. drift      — built-in scores within ±TOLERANCE of the saved baseline for the same HEURISTIC_VERSION
The deterministic part also runs inside pytest (tests/test_app.py::test_scoring_benchmark).
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BENCH = ROOT / "tests" / "benchmark"
TOLERANCE = 3
TIERS = {"weak": 0, "medium": 1, "strong": 2}


def load_cases():
    return json.loads((BENCH / "cases.json").read_text())["cases"]


def score_case(case, live=False):
    from coach import analysis
    if not live:
        os.environ["LLM_PROVIDER"] = "local"
    a = analysis.analyze(case["transcript"], case["mode"], case["prompt"], False, None)
    return {"overall": a["overall_score"], "scores": a["scores"],
            "present": [x["label"] for x in a["structure_map"] if x["status"] == "present"],
            "verdict": a["review"]["evaluator"]["verdict"], "provider": a["provider"],
            "basis": a["provenance"]["score_basis"]}


def evaluate(results, cases, baseline=None):
    problems, by_group = [], {}
    for c in cases:
        by_group.setdefault(c["group"], []).append(c)
    for group, items in by_group.items():
        items = sorted(items, key=lambda c: TIERS[c["tier"]])
        for lo, hi in zip(items, items[1:]):
            if results[lo["id"]]["overall"] >= results[hi["id"]]["overall"]:
                problems.append(f"ordering: {hi['id']} ({results[hi['id']]['overall']}) should beat "
                                f"{lo['id']} ({results[lo['id']]['overall']})")
    for c in cases:
        r, e = results[c["id"]], c.get("expect", {})
        for part in e.get("present", []):
            if part not in r["present"]:
                problems.append(f"framework: {c['id']} should include {part!r}")
        for part in e.get("missing", []):
            if part in r["present"]:
                problems.append(f"framework: {c['id']} should be missing {part!r}")
        if e.get("verdict_in") and r["verdict"] not in e["verdict_in"]:
            problems.append(f"verdict: {c['id']} got {r['verdict']}, expected one of {e['verdict_in']}")
        if baseline and c["id"] in baseline["cases"]:
            b = baseline["cases"][c["id"]]
            for d, v in {"overall": r["overall"], **r["scores"]}.items():
                bv = b["overall"] if d == "overall" else b["scores"].get(d)
                if bv is not None and abs(v - bv) > TOLERANCE:
                    problems.append(f"drift: {c['id']} {d} {bv} → {v} (>±{TOLERANCE}). If intentional, bump "
                                    f"HEURISTIC_VERSION and run --update-baseline.")
    return problems


def run_local(update=False):
    from coach import scoring
    cases = load_cases()
    results = {c["id"]: score_case(c) for c in cases}
    path = BENCH / "baseline_local.json"
    baseline = json.loads(path.read_text()) if path.exists() else None
    if baseline and baseline.get("heuristic_version") != scoring.HEURISTIC_VERSION and not update:
        return results, [f"baseline is for heuristic {baseline.get('heuristic_version')}, code is "
                         f"{scoring.HEURISTIC_VERSION} — run --update-baseline"]
    if update:
        path.write_text(json.dumps({"heuristic_version": scoring.HEURISTIC_VERSION,
                                    "cases": {k: {"overall": v["overall"], "scores": v["scores"]}
                                              for k, v in results.items()}}, indent=1) + "\n")
        baseline = None
    return results, evaluate(results, cases, baseline)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="also evaluate the configured AI provider")
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args()
    results, problems = run_local(args.update_baseline)
    print(f"Built-in scorer: {len(results)} cases, {len(problems)} problem(s)")
    for p in problems:
        print("  ✗", p)
    status = 1 if problems else 0
    if args.live:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        from coach import llm
        if llm.active_provider() == "local":
            print("Live: no AI provider configured — skipped.")
        else:
            cases = load_cases()
            live = {c["id"]: score_case(c, live=True) for c in cases}
            lp = evaluate(live, cases)
            ai = sum(1 for v in live.values() if v["basis"] == "ai")
            print(f"Live ({llm.active_provider()}): {ai}/{len(cases)} scored by AI, {len(lp)} problem(s)")
            for p in lp:
                print("  ✗", p)
            mae = sum(abs(live[k]["overall"] - results[k]["overall"]) for k in live) / len(live)
            print(f"  mean |AI − built-in| overall: {mae:.1f} (informational — different scales are expected)")
            status = status or (1 if lp else 0)
    sys.exit(status)


if __name__ == "__main__":
    main()
