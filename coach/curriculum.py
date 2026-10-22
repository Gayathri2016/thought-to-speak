"""Lessons (journeys → modules → lessons), pass criteria, and the daily plan."""
import json
import random
from datetime import datetime, time, timezone
from pathlib import Path

from . import insights
from . import language as lang
from . import pronunciation as pron
from .prompts import MODES

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content" / "journeys"


class Curriculum:
    def __init__(self, content_dir: Path = CONTENT_DIR):
        self.journeys = []
        for f in sorted(Path(content_dir).glob("*.json")):
            self.journeys.append(json.loads(f.read_text(encoding="utf-8")))
        self.lessons, self.order = {}, []
        for j in self.journeys:
            for mod in j["modules"]:
                for les in mod["lessons"]:
                    self._validate(les)
                    self.lessons[les["id"]] = {**les, "journey_id": j["id"], "module_id": mod["id"],
                                               "module_title": mod["title"]}
                    self.order.append(les["id"])

    @staticmethod
    def _validate(les):
        for key in ("id", "title", "concept", "framework", "exercise"):
            if key not in les:
                raise ValueError(f"Lesson {les.get('id')} missing '{key}'")
        ex = les["exercise"]
        if ex.get("kind") == "read_aloud":
            if not pron.get_set(ex.get("set", "")):
                raise ValueError(f"Lesson {les['id']} uses unknown pronunciation set {ex.get('set')!r}")
            return
        mode = ex["mode"]
        if mode not in MODES:
            raise ValueError(f"Lesson {les['id']} has unknown mode {mode!r}")

    # ------------------------------------------------------------------ specs
    def get(self, lesson_id: str) -> dict | None:
        return self.lessons.get(lesson_id)

    @staticmethod
    def is_read_aloud(lesson: dict | None) -> bool:
        return bool(lesson and lesson["exercise"].get("kind") == "read_aloud")

    def read_aloud_texts(self, lesson: dict) -> list[str]:
        return [i["text"] for i in pron.get_set(lesson["exercise"]["set"])["items"]]

    def spec(self, lesson: dict) -> dict:
        """Analysis override: score against the lesson's framework, not the mode's default."""
        fw = lesson["framework"]
        ex = lesson["exercise"]
        rubric = "; ".join(f"{s['label']}: {s['desc']}" for s in fw["steps"])
        return {"name": fw["name"], "framework": [s["label"] for s in fw["steps"]],
                "rubric": f"{rubric}. Exercise constraint: {ex.get('constraint', '')}",
                "goal": f"Lesson '{lesson['title']}': {lesson.get('summary', '')}",
                "target_seconds": tuple(ex.get("target_seconds") or MODES[ex["mode"]]["target_seconds"])}

    @staticmethod
    def _metric(analysis: dict, path: str):
        if path == "overall":
            return analysis.get("overall_score")
        if path == "framework_coverage":
            return analysis.get("framework_coverage")
        cur = analysis
        for part in path.split("."):
            cur = (cur or {}).get(part) if isinstance(cur, dict) else None
        return cur

    def evaluate(self, lesson: dict, analysis: dict) -> dict:
        rules = lesson["exercise"].get("pass", {})
        need = rules.get("overall", 60)
        checks = [{"label": f"Overall ≥ {need}", "ok": analysis["overall_score"] >= need,
                   "actual": analysis["overall_score"]}]
        ops = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
               "len<=": lambda a, b: len(a or []) <= b, "len>=": lambda a, b: len(a or []) >= b}
        for c in rules.get("checks", []):
            actual = self._metric(analysis, c["metric"])
            if c.get("audio") and not analysis.get("voice"):
                checks.append({"label": c["label"], "ok": True, "actual": "not checked — record with the mic",
                               "skipped": True})
                continue
            if c.get("requires") == "sentences" and \
                    (analysis.get("language") or {}).get("sentences", {}).get("method") == "none":
                checks.append({"label": c["label"], "ok": True, "skipped": True,
                               "actual": "not checked — needs punctuation or a Whisper transcript"})
                continue
            if c["op"].startswith("len"):
                ok = ops[c["op"]](actual, c["value"])
                checks.append({"label": c["label"], "ok": bool(ok), "actual": len(actual or []),
                               "detail": [x if isinstance(x, str) else x.get("original") or x.get("phrase") or str(x)
                                          for x in (actual or [])][:3]})
                continue
            ok = actual is not None and ops[c["op"]](actual, c["value"])
            if isinstance(actual, float) and c["metric"] == "framework_coverage":
                actual = f"{round(actual * 100)}%"
            checks.append({"label": c["label"], "ok": bool(ok), "actual": actual})
        return {"lesson_id": lesson["id"], "passed": all(c["ok"] for c in checks), "checks": checks}

    # ------------------------------------------------------------------ progress
    def journey_view(self, stats: dict) -> list[dict]:
        out = []
        for j in self.journeys:
            ids = [les["id"] for mod in j["modules"] for les in mod["lessons"]]
            current = next((i for i in ids if not stats.get(i, {}).get("passed")), None)
            mods, done, total = [], 0, 0
            for mod in j["modules"]:
                items = []
                for les in mod["lessons"]:
                    st = stats.get(les["id"], {})
                    status = "done" if st.get("passed") else "current" if les["id"] == current else \
                        "started" if st.get("attempts") else "upcoming"
                    done += status == "done"
                    total += 1
                    items.append({"id": les["id"], "title": les["title"], "summary": les.get("summary", ""),
                                  "minutes": les.get("minutes"), "skill": les.get("skill"),
                                  "mode": les["exercise"]["mode"], "framework": les["framework"]["name"],
                                  "kind": les["exercise"].get("kind", "speak"),
                                  "status": status, "best": st.get("best"), "attempts": st.get("attempts", 0)})
                mods.append({"id": mod["id"], "title": mod["title"], "goal": mod.get("goal", ""), "lessons": items})
            out.append({"id": j["id"], "title": j["title"], "description": j.get("description", ""),
                        "modules": mods, "done": done, "total": total})
        return out

    def current_lesson_id(self, stats: dict) -> str | None:
        for lid in self.order:
            if not stats.get(lid, {}).get("passed"):
                return lid
        return None

    # ------------------------------------------------------------------ daily plan
    def today(self, store) -> dict:
        now = datetime.now().astimezone()
        day = now.date().isoformat()
        start_utc = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo).astimezone(timezone.utc)
        todays = store.since(start_utc.isoformat(timespec="seconds"))
        stats = store.lesson_stats()
        all_attempts = store.all_chronological()

        plan = store.meta_get(f"plan:{day}")
        if not plan:
            rng = random.Random(day)
            warm_prompt = rng.choice(MODES["Toastmasters"]["prompts"])
            rec = insights.recommend(all_attempts)
            sounds = insights.sound_stats(all_attempts)
            weak = [g for g, v in sorted(sounds.items(), key=lambda kv: kv[1]["accuracy"]) if v["accuracy"] < 90]
            sets = [st for st in pron.drill_sets() if st.get("group") in weak[:1]] or pron.drill_sets()
            dset = sets[0] if weak else rng.choice(sets)
            plan = {"warmup_prompt": warm_prompt, "review": rec,
                    "lesson_id": self.current_lesson_id(stats),
                    "drill": {"set": dset["id"], "title": dset["title"], "text": rng.choice(dset["items"])["text"],
                              "reason": f"Your weakest sound lately: {weak[0]}" if weak else dset["description"]},
                    "words": [w["word"] for w in lang.daily_words(now.date())]}
            store.meta_set(f"plan:{day}", plan)

        lesson_id = plan.get("lesson_id") or self.current_lesson_id(stats)
        if lesson_id is None and self.order:  # journey finished → replay the weakest lesson
            lesson_id = min(self.order, key=lambda lid: stats.get(lid, {}).get("best") or 0)
        lesson = self.get(lesson_id) if lesson_id else None

        def done(kind, lid=None):
            return any(a.get("kind") == kind and (lid is None or a.get("lesson_id") == lid) for a in todays)

        lesson_passed_today = any(a.get("lesson_id") == lesson_id and a.get("lesson_passed") for a in todays)
        rec = plan["review"]
        items = [
            {"key": "warmup", "kind": "warmup", "title": "Warm-up: 30-second impromptu",
             "detail": "No prep. Hit record and answer straight away — a fast start beats a perfect one.",
             "mode": "Toastmasters", "prompt": plan["warmup_prompt"], "minutes": 2,
             "target_seconds": [20, 40], "challenge_mode": False, "done": done("warmup")},
        ]
        if lesson:
            items.append({
                "key": "lesson", "kind": "lesson", "title": f"Lesson: {lesson['title']}",
                "detail": lesson.get("summary", ""), "lesson_id": lesson["id"], "mode": lesson["exercise"]["mode"],
                "minutes": lesson.get("minutes", 8),
                "done": lesson_passed_today or bool(stats.get(lesson["id"], {}).get("passed") and done("lesson", lesson["id"])),
                "attempted": done("lesson", lesson["id"]),
            })
        if plan.get("drill"):
            d = plan["drill"]
            items.append({
                "key": "pronounce", "kind": "read_aloud", "title": f"Pronunciation: {d['title']}",
                "detail": d["reason"], "mode": "Read aloud", "prompt": d["text"], "set": d["set"],
                "minutes": 2, "done": done("read_aloud"),
            })
        items.append({
            "key": "review", "kind": "review", "title": f"Review: {rec['title']}",
            "detail": rec.get("evidence") or rec.get("why", ""), "mode": rec["mode"], "prompt": rec["prompt"],
            "constraint": rec.get("constraint"), "steps": rec.get("steps", []), "minutes": 4,
            "challenge_mode": rec.get("challenge_mode", True), "done": done("review"),
        })
        total_min = sum(i["minutes"] for i in items)
        words = [w for w in lang.word_bank() if w["word"] in (plan.get("words") or [])]
        used_today = {w for a in todays for w in ((a["analysis"].get("language") or {}).get("vocabulary") or {}).get("daily_used", [])}
        return {"date": day, "items": items, "minutes": total_min,
                "words": [{**w, "used": w["word"] in used_today} for w in words],
                "completed": sum(1 for i in items if i["done"]), "total": len(items),
                "lessons_done": sum(1 for s in stats.values() if s.get("passed")), "lessons_total": len(self.order)}
