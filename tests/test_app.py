import importlib
import json

import pytest
from fastapi.testclient import TestClient

PROMPT = "Tell me about a time you disagreed with a senior stakeholder."
WEAK = ("um so like I was on a team and uh we had a stakeholder who you know disagreed "
        "I think maybe we had issues and basically we worked it out")
STRONG = ("When I was leading the streaming platform migration last year, my director disagreed with my plan "
          "to move to Flink. My role was to deliver the migration by Q3. I decided to run a two week proof of "
          "concept and I shared the latency data with him. As a result we reduced latency by 40 percent and he "
          "approved the plan. I learned that data wins disagreements better than opinions.")


def _fresh_app(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("TTS_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("TRANSCRIBE_PROVIDER", "none")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("DAILY_TAKE_LIMIT", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from coach import auth
    for lim in (auth.login_failures, auth.signups):
        lim._hits.clear()
    import app as app_module
    importlib.reload(app_module)
    return app_module


def _signup(c, email="ada@example.com", password="correct horse"):
    r = c.post("/api/auth/signup", json={"email": email, "password": password, "name": "Ada"})
    assert r.status_code == 200, r.text
    return r.json()["user"]


@pytest.fixture()
def anon(tmp_path, monkeypatch):
    """A client with no account yet."""
    return TestClient(_fresh_app(tmp_path, monkeypatch).app)


@pytest.fixture()
def client(anon):
    """A signed-in client."""
    _signup(anon)
    return anon


def test_config_and_prompt(client):
    cfg = client.get("/api/config").json()
    assert cfg["provider"] == "local"
    assert len(cfg["modes"]) == 5
    p = client.get("/api/prompt", params={"mode": "Toastmasters"}).json()
    assert p["mode"] == "Toastmasters" and p["prompt"]


def test_analyze_rejects_empty(client):
    assert client.post("/api/analyze", json={"transcript": "hi"}).status_code == 400


def test_metrics_counts_fillers_not_verbs():
    from coach.metrics import compute
    m = compute("I would like to explain. Um, it was like, you know, basically fine.")
    assert m["fillers"].get("like") == 1          # 'would like' is not a filler
    assert m["fillers"]["um"] == 1 and m["fillers"]["you know"] == 1


def test_retry_loop_compare_and_progress(client):
    r1 = client.post("/api/analyze", json={"transcript": WEAK, "mode": "Interview", "prompt": PROMPT,
                                           "duration_seconds": 20}).json()
    assert r1["attempt"]["attempt_no"] == 1 and r1["comparison"] is None
    r2 = client.post("/api/analyze", json={"transcript": STRONG, "mode": "Interview", "prompt": PROMPT,
                                           "duration_seconds": 35, "chain_id": r1["attempt"]["chain_id"]}).json()
    assert r2["attempt"]["attempt_no"] == 2
    c = r2["comparison"]
    assert c["verdict"] == "improved" and c["deltas"]["overall"] > 0
    assert r2["analysis"]["overall_score"] > r1["analysis"]["overall_score"]

    p = client.get("/api/progress").json()
    assert p["total_attempts"] == 2 and p["retry_chains"] == 1 and p["avg_retry_gain"] > 0
    rec = p["recommendation"]
    assert rec["kind"] == "weak_area" and rec["dimension"] in p["dimension_avgs"]
    assert rec["prompt"]

    hist = client.get("/api/history").json()
    assert len(hist) == 2
    assert client.delete(f"/api/attempts/{hist[0]['id']}").json()["ok"]
    assert len(client.get("/api/history").json()) == 1


def test_baseline_recommendation_when_empty(client):
    assert client.get("/api/recommendation").json()["kind"] == "baseline"


def test_llm_path_and_challenge_guard(client, monkeypatch):
    from coach import analysis, llm

    fake = {"scores": {"structure": 81, "clarity": 77, "relevance": 90, "answer_quality": 70},
            "structure_map": [{"label": "Situation", "status": "present", "note": "ok"}],
            "strengths": ["Clear context."], "improvements": ["Quantify more."],
            "coaching_hint": "Lead with the result.", "coaching_questions": ["What changed?"],
            "next_challenge": "Do it in 60s.", "suggested_outline": ["Situation: migration"]}
    monkeypatch.setattr(llm, "active_provider", lambda: "openai")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (json.loads(json.dumps(fake)), "openai:test"))

    a = analysis.analyze(STRONG, "Interview", PROMPT, challenge=True, duration_seconds=30)
    assert a["provider"] == "openai:test"
    assert a["scores"]["structure"] == 81 and "delivery" in a["scores"]
    assert a["suggested_outline"] == []            # challenge mode never returns an outline/rewrite
    b = analysis.analyze(STRONG, "Interview", PROMPT, challenge=False)
    assert b["suggested_outline"] == ["Situation: migration"]


def test_llm_failure_falls_back(monkeypatch):
    from coach import analysis, llm

    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(llm, "active_provider", lambda: "openai")
    monkeypatch.setattr(llm, "complete_json", boom)
    a = analysis.analyze(STRONG, "Interview", PROMPT)
    assert a["provider"] == "local:heuristic" and a["warning"]


def test_legacy_sessions_import(tmp_path):
    from coach.storage import Store
    legacy = tmp_path / "sessions.json"
    legacy.write_text(json.dumps([{"mode": "Toastmasters", "transcript": "hello there friends",
                                   "analysis": {"overall_score": 61, "structure_score": 50, "clarity_score": 70},
                                   "created_at": "2026-10-05T19:45:00"}]))
    s = Store(tmp_path / "db.sqlite", legacy_json=legacy)
    Store(tmp_path / "db.sqlite", legacy_json=legacy)   # second start must not re-import
    rows = s.all_chronological()
    assert len(rows) == 1 and rows[0]["overall"] == 61 and rows[0]["structure"] == 50


BLUF = ("No, we'll ship Monday, not Friday. The short answer is that upstream changed their schema because of "
        "a migration, and QA found one blocking bug. Next step: I need a thirty minute review from you on Monday.")


def test_curriculum_content_is_valid():
    from coach.curriculum import Curriculum
    from coach.prompts import MODES
    cur = Curriculum()
    assert len(cur.order) == 19 and len(cur.journeys) == 2
    for lid in cur.order:
        les = cur.get(lid)
        if cur.is_read_aloud(les):
            assert cur.read_aloud_texts(les)
            continue
        spec = cur.spec(les)
        assert spec["framework"] and les["exercise"]["mode"] in MODES and les["exercise"]["prompts"]


def test_lesson_pass_unlocks_next_and_today_plan(client):
    today = client.get("/api/today").json()
    assert [i["kind"] for i in today["items"]] == ["warmup", "lesson", "read_aloud", "review"]
    assert len(today["words"]) == 3
    assert today["items"][1]["lesson_id"] == "c1-bluf"

    fail = client.post("/api/analyze", json={"transcript": WEAK, "lesson_id": "c1-bluf",
                                             "prompt": "Status update on a late project"}).json()
    assert fail["lesson_result"]["passed"] is False
    ok = client.post("/api/analyze", json={"transcript": BLUF, "lesson_id": "c1-bluf", "duration_seconds": 30,
                                           "prompt": "Status update on a late project",
                                           "chain_id": fail["attempt"]["chain_id"]}).json()
    assert ok["lesson_result"]["passed"] is True
    assert ok["analysis"]["framework_name"] == "BLUF"
    assert ok["attempt"]["kind"] == "lesson" and ok["attempt"]["mode"] == "Leadership"

    statuses = {l["id"]: l["status"] for m in client.get("/api/journey").json()[0]["modules"] for l in m["lessons"]}
    assert statuses["c1-bluf"] == "done" and statuses["c2-prep"] == "current"

    client.post("/api/analyze", json={"transcript": STRONG, "kind": "warmup", "mode": "Toastmasters"})
    items = {i["key"]: i for i in client.get("/api/today").json()["items"]}
    assert items["lesson"]["done"] and items["warmup"]["done"] and not items["review"]["done"]


def test_unknown_lesson_404(client):
    assert client.post("/api/analyze", json={"transcript": STRONG, "lesson_id": "nope"}).status_code == 404


def test_vague_words_metric():
    from coach.metrics import compute
    m = compute("We did a lot of stuff and things were really nice.")
    assert m["vague_count"] >= 4


def _speechlike_wav(seconds_pattern):
    """Synthetic 'speech': voiced harmonic bursts with gliding pitch, separated by silences."""
    import numpy as np
    from coach import audio
    sr, rng, parts = 16000, np.random.default_rng(1), []
    for kind, dur in seconds_pattern:
        n = int(dur * sr)
        if kind == "s":
            t = np.arange(n) / sr
            f0 = 120 + 40 * np.sin(2 * np.pi * 0.7 * t)          # ~6 semitone glide → not monotone
            ph = 2 * np.pi * np.cumsum(f0) / sr
            sig = sum(np.sin(k * ph) / k for k in range(1, 6))
            env = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t) ** 2       # syllable-rate modulation
            parts.append((0.2 * sig * env).astype(np.float32))
        else:
            parts.append(np.zeros(n, dtype=np.float32))
    x = np.concatenate(parts) + rng.normal(0, 0.002, sum(len(p) for p in parts)).astype(np.float32)
    return audio.to_wav_bytes(x)


def test_audio_analysis_detects_pauses_and_stall():
    from coach import audio
    x = audio.load_audio(_speechlike_wav([("q", .5), ("s", 4), ("q", .7), ("s", 4), ("q", 3.2), ("s", 4), ("q", .5)]))
    a = audio.analyze(x, word_count=30)
    kinds = [p["kind"] for p in a["pauses"]]
    assert "stall" in kinds and a["stalls"] == 1
    assert any(abs(p["duration"] - 0.7) < 0.25 for p in a["pauses"])
    assert a["pitch"]["label"] in ("limited", "expressive")
    assert a["speaking_window"][0] >= 0.4      # leading silence trimmed
    assert set(audio.voice_scores(a)) == {"pace", "pauses", "pitch_variety", "volume"}


def test_analyze_audio_endpoint_uses_whisper_words(client, monkeypatch):
    from coach import transcribe
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(STRONG.split()[:30])]
    monkeypatch.setattr(transcribe, "transcribe", lambda p: {"text": STRONG, "words": words, "source": "test:whisper"})
    wav = _speechlike_wav([("q", .5), ("s", 5), ("q", 1.2), ("s", 6), ("q", .5)])
    r = client.post("/api/analyze-audio", files={"audio": ("take.wav", wav, "audio/wav")},
                    data={"payload": json.dumps({"transcript": "browser text here please", "mode": "Interview"})})
    assert r.status_code == 200, r.text
    body = r.json()
    v = body["analysis"]["voice"]
    assert v["transcript_source"] == "test:whisper" and v["timestamped_words"]
    assert body["attempt"]["transcript"] == STRONG          # Whisper text replaces the browser transcript
    assert client.get(f"/api/audio/{body['attempt']['id']}").status_code == 200
    client.delete(f"/api/attempts/{body['attempt']['id']}")
    assert client.get(f"/api/audio/{body['attempt']['id']}").status_code == 404


def test_analyze_audio_falls_back_to_browser_transcript(client, monkeypatch):
    from coach import transcribe
    monkeypatch.setattr(transcribe, "transcribe", lambda p: None)
    wav = _speechlike_wav([("q", .3), ("s", 5), ("q", .3)])
    r = client.post("/api/analyze-audio", files={"audio": ("take.wav", wav, "audio/wav")},
                    data={"payload": json.dumps({"transcript": STRONG, "mode": "Interview"})}).json()
    assert r["analysis"]["voice"]["transcript_source"] == "browser speech-to-text"
    silent = _speechlike_wav([("q", 3)])
    bad = client.post("/api/analyze-audio", files={"audio": ("x.wav", silent, "audio/wav")},
                      data={"payload": json.dumps({"transcript": STRONG})})
    assert bad.status_code == 400


# ----------------------------------------------------------------------------- v5: pronunciation & language
def test_stress_and_respelling():
    from coach.pronunciation import describe
    d = describe("development")
    assert d["known"] and d["syllables"] == 4 and d["stress"] == "2nd of 4 syllables" and "VEH" in d["respell"]
    assert describe("think")["respell"] == "think" and "TH" in describe("think")["sounds"]
    assert describe("kubernetes")["known"]          # custom technical lexicon


def test_read_aloud_detects_swaps_and_sound_confusions():
    from coach.pronunciation import assess_read_aloud
    a = assess_read_aloud("I think three things went through the thread on Thursday.",
                          "I sink three tings went through the um thread on Thursday")
    assert a["total"] == 10 and a["correct"] == 8 and a["accuracy"] == 80
    assert {"th → s", "th → t"} <= set(a["confusions"])
    assert a["sounds"]["TH"][1] >= 5 and a["sounds"]["TH"][0] < a["sounds"]["TH"][1]
    missed = assess_read_aloud("Please sit in this seat", "please sit this seat")
    assert missed["missed"] == ["in"]
    words = [{"word": w, "start": i, "end": i + .3, "probability": p}
             for i, (w, p) in enumerate(zip("very well we will".split(), [.9, .3, .9, .9]))]
    assert assess_read_aloud("Very well we will", "", words)["unclear"] == ["well"]


def test_vocabulary_and_sentence_analysis():
    from coach import language as L
    weak = ("So we had a lot of stuff going on. So it was very good. So we fixed it and then it was fine "
            "and then we shipped it and so it worked and then everyone was happy.")
    v, s = L.vocabulary(weak, ["pragmatic"]), L.sentences(weak)
    assert {w["phrase"] for w in v["weak"]} >= {"a lot of", "stuff", "very good"}
    assert s["repeated_opener"] == "so" and s["run_ons"]
    strong = ("When the pipeline missed its SLA, I profiled it and found a skewed join. I salted the key, "
              "which eliminated the hotspot. The pragmatic lesson: measure before you optimize.")
    v2, s2 = L.vocabulary(strong, ["pragmatic"]), L.sentences(strong)
    assert v2["score"] > v["score"] and "pragmatic" in v2["daily_used"] and s2["score"] > s["score"]
    issues = {i["original"] for i in L.grammar_issues("He don't know. There's many bugs. It took a hour.")}
    assert {"he don't", "there's many", "a hour"} <= issues


def test_read_aloud_endpoint_and_th_lesson(client):
    target = "I think three things went through the thread on Thursday."
    r = client.post("/api/analyze", json={"transcript": "I sink three things went through the thread on Thursday",
                                          "kind": "read_aloud", "target_text": target}).json()
    assert r["analysis"]["kind"] == "read_aloud" and r["analysis"]["pronunciation"]["confusions"] == ["th → s"]
    ok = client.post("/api/analyze", json={"transcript": target, "lesson_id": "s4-th", "target_text": target}).json()
    assert ok["lesson_result"]["passed"] and ok["attempt"]["mode"] == "Read aloud"
    p = client.get("/api/progress").json()
    assert p["language"]["read_aloud_count"] == 2 and "TH" in p["language"]["sounds"]
    assert p["trend"] == []                          # drills don't pollute the speaking-score trend
    assert client.get("/api/pronounce/lookup", params={"words": "photography"}).json()[0]["stress"]
    assert len(client.get("/api/pronounce/sets").json()) == 7


def test_challenge_mode_hides_word_replacements(client):
    r = client.post("/api/analyze", json={"transcript": "It was very good and he don't mind, a lot of stuff.",
                                          "mode": "Interview", "challenge_mode": True}).json()
    L = r["analysis"]["language"]
    assert all(not w["alternatives"] for w in L["vocabulary"]["weak"])
    assert all(g["suggestion"] is None for g in L["sentences"]["grammar"])


def test_llm_language_notes_merge_and_respect_challenge(monkeypatch):
    from coach import analysis, llm
    fake = {"scores": {"structure": 70, "clarity": 70, "relevance": 70, "answer_quality": 70},
            "language": {"word_upgrades": [{"original": "good", "better": "effective", "why": "more precise"}],
                         "grammar": [{"original": "we was", "corrected": "we were", "issue": "agreement"}],
                         "sentence_tip": "Split your second sentence."}}
    monkeypatch.setattr(llm, "active_provider", lambda: "openai")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (json.loads(json.dumps(fake)), "openai:test"))
    a = analysis.analyze(STRONG, "Interview", PROMPT)
    assert a["language"]["word_upgrades"][0]["better"] == "effective"
    assert any(g.get("source") == "ai" and g["suggestion"] == "we were" for g in a["language"]["sentences"]["grammar"])
    c = analysis.analyze(STRONG, "Interview", PROMPT, challenge=True)
    assert c["language"]["word_upgrades"][0]["better"] == ""
    assert all(g["suggestion"] is None for g in c["language"]["sentences"]["grammar"])


# ----------------------------------------------------------------------------- v6: accounts
def test_data_endpoints_require_sign_in(anon):
    assert anon.get("/api/auth/me").json() == {"mode": "accounts", "user": None}
    for path in ("/api/history", "/api/progress", "/api/today", "/api/journey", "/api/export", "/api/recommendation"):
        assert anon.get(path).status_code == 401, path
    assert anon.post("/api/analyze", json={"transcript": STRONG}).status_code == 401
    # public, account-free endpoints still work (landing page, pronunciation lookup)
    assert anon.get("/api/config").status_code == 200
    assert anon.get("/api/pronounce/lookup", params={"words": "data"}).status_code == 200


def test_each_user_sees_only_their_own_practice(tmp_path, monkeypatch):
    app_module = _fresh_app(tmp_path, monkeypatch)
    ada, bob = TestClient(app_module.app), TestClient(app_module.app)
    _signup(ada, "ada@example.com")
    _signup(bob, "bob@example.com")
    r = ada.post("/api/analyze", json={"transcript": STRONG, "mode": "Interview", "prompt": PROMPT}).json()
    aid, chain = r["attempt"]["id"], r["attempt"]["chain_id"]

    assert len(ada.get("/api/history").json()) == 1
    assert bob.get("/api/history").json() == []
    assert bob.get("/api/progress").json()["total_attempts"] == 0
    assert bob.get(f"/api/chain/{chain}").json() == []
    assert bob.get(f"/api/audio/{aid}").status_code == 404
    assert bob.delete(f"/api/attempts/{aid}").status_code == 404          # can't delete someone else's take
    assert bob.get("/api/export").json() == []
    # retrying onto another user's chain starts a fresh chain instead of joining theirs
    rb = bob.post("/api/analyze", json={"transcript": STRONG, "chain_id": chain}).json()
    assert rb["attempt"]["chain_id"] != chain and rb["attempt"]["attempt_no"] == 1
    assert len(ada.get("/api/history").json()) == 1


def test_signup_login_logout_and_errors(anon):
    assert anon.post("/api/auth/signup", json={"email": "nope", "password": "longenough"}).status_code == 400
    assert anon.post("/api/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code == 400
    user = _signup(anon, "Ada@Example.com ")
    assert user["email"] == "ada@example.com"                               # normalized
    assert anon.post("/api/auth/signup", json={"email": "ADA@example.com", "password": "another pw"}).status_code == 409

    assert anon.post("/api/auth/logout").json()["ok"]
    assert anon.get("/api/history").status_code == 401
    assert anon.post("/api/auth/login", json={"email": "ada@example.com", "password": "wrong pass"}).status_code == 401
    assert anon.post("/api/auth/login", json={"email": "ghost@example.com", "password": "whatever1"}).status_code == 401
    ok = anon.post("/api/auth/login", json={"email": "ADA@example.com", "password": "correct horse"})
    assert ok.status_code == 200 and ok.json()["user"]["id"] == user["id"]
    assert anon.get("/api/auth/me").json()["user"]["email"] == "ada@example.com"


def test_session_cookie_is_httponly_and_token_not_stored(anon, tmp_path):
    r = anon.post("/api/auth/signup", json={"email": "ada@example.com", "password": "correct horse"})
    cookie = r.headers["set-cookie"]
    assert "tts_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    token = anon.cookies.get("tts_session")
    import sqlite3
    db = sqlite3.connect(tmp_path / "t.db")
    hashes = [row[0] for row in db.execute("SELECT token_hash FROM sessions")]
    pw = db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert token not in hashes and len(hashes) == 1                         # only the hash is stored
    assert pw.startswith("scrypt$") and "correct horse" not in pw


def test_login_is_rate_limited(anon):
    _signup(anon)
    anon.post("/api/auth/logout")
    for _ in range(10):
        anon.post("/api/auth/login", json={"email": "ada@example.com", "password": "wrong pass"})
    r = anon.post("/api/auth/login", json={"email": "ada@example.com", "password": "correct horse"})
    assert r.status_code == 429


def test_delete_account_removes_everything(tmp_path, monkeypatch):
    from coach import transcribe
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    monkeypatch.setattr(transcribe, "transcribe", lambda p: None)
    wav = _speechlike_wav([("q", .3), ("s", 5), ("q", .3)])
    r = c.post("/api/analyze-audio", files={"audio": ("take.wav", wav, "audio/wav")},
               data={"payload": json.dumps({"transcript": STRONG, "mode": "Interview"})}).json()
    audio_file = app_module.AUDIO_DIR / r["attempt"]["audio_path"]
    assert audio_file.exists()
    assert c.request("DELETE", "/api/auth/account", json={"password": "wrong"}).status_code == 403
    assert c.request("DELETE", "/api/auth/account", json={"password": "correct horse"}).json()["ok"]
    assert not audio_file.exists()
    assert c.get("/api/history").status_code == 401
    assert c.post("/api/auth/login", json={"email": "ada@example.com", "password": "correct horse"}).status_code == 401


def test_daily_take_limit(tmp_path, monkeypatch):
    c = TestClient(_fresh_app(tmp_path, monkeypatch, DAILY_TAKE_LIMIT="2").app)
    _signup(c)
    for _ in range(2):
        assert c.post("/api/analyze", json={"transcript": STRONG}).status_code == 200
    r = c.post("/api/analyze", json={"transcript": STRONG})
    assert r.status_code == 429 and "limit" in r.json()["detail"]


def test_first_account_inherits_practice_from_before_accounts(tmp_path, monkeypatch):
    from coach.storage import Store
    old = Store(tmp_path / "t.db")
    old.add_attempt(mode="Interview", prompt="p", transcript=STRONG, challenge_mode=False, duration_seconds=30,
                    analysis={"overall_score": 70})
    app_module = _fresh_app(tmp_path, monkeypatch)
    first, second = TestClient(app_module.app), TestClient(app_module.app)
    _signup(first, "me@example.com")
    _signup(second, "friend@example.com")
    assert len(first.get("/api/history").json()) == 1
    assert second.get("/api/history").json() == []


def test_single_user_mode_needs_no_sign_in(tmp_path, monkeypatch):
    c = TestClient(_fresh_app(tmp_path, monkeypatch, AUTH_MODE="single").app)
    assert c.get("/api/auth/me").json()["mode"] == "single"
    assert c.post("/api/analyze", json={"transcript": STRONG}).status_code == 200
    assert len(c.get("/api/history").json()) == 1
    assert c.post("/api/auth/signup", json={"email": "a@b.co", "password": "longenough"}).status_code == 400


def test_app_files_are_revalidated_not_cached(anon):
    for path in ("/", "/static/app.js", "/static/app.css"):
        assert anon.get(path).headers["cache-control"] == "no-cache", path


# ----------------------------------------------------------------------------- v7: 28-day Speaking Sprint
INTRO = ("I'm a backend engineer who builds payment systems for a retail company. I've been there three years; "
         "before that I worked on rewards, and I built the redemption service that cut failed redemptions by "
         "40 percent. Right now I'm looking for a role where I can lead platform work, and I'm excited about "
         "this team.")


def _days(state):
    return [d for w in state["weeks"] for d in w["days"]]


def test_program_content_is_complete_and_scorable():
    from coach.analysis import FRAMEWORK_CUES
    from coach.curriculum import Curriculum
    from coach.prompts import MODES
    cur = Curriculum()
    assert list(cur.programs) == ["speaking-sprint-28"]
    ids = cur.program_days["speaking-sprint-28"]
    assert len(ids) == 28 and len(cur.order) == 19          # program days don't change the self-paced journeys
    for n, lid in enumerate(ids, start=1):
        les = cur.get(lid)
        assert les["day"] == n and les["exercise"]["mode"] in MODES and len(les["exercise"]["prompts"]) == 3
        assert les["example"]["before"] and les["example"]["after"] and les["cheat_sheet"]
        for step in les["framework"]["steps"]:                # local scoring can detect every framework part
            assert FRAMEWORK_CUES.get(step["label"]), (lid, step["label"])
    titles = [cur.get(i)["title"] for i in ids]
    assert titles[0] == "Your 30-second story" and titles[-1].startswith("Final")
    assert len(set(titles)) == 28
    assert [m["day"] for m in cur.programs["speaking-sprint-28"]["milestones"]] == [7, 14, 21, 28]


def test_program_unlocks_one_day_per_calendar_day(client, monkeypatch):
    import app as app_module
    from datetime import datetime, timedelta
    st = client.get("/api/programs").json()[0]
    assert st["title"] == "The 28-Day Speaking Sprint" and not st["enrolled"]
    days = _days(st)
    assert days[0]["status"] == "available" and days[1]["status"] == "locked"

    # Day 2 can't be practised before Day 1 is passed
    r = client.post("/api/analyze", json={"transcript": INTRO, "lesson_id": "ss02"})
    assert r.status_code == 403 and "Day 1" in r.json()["detail"]

    ok = client.post("/api/analyze", json={"transcript": INTRO, "lesson_id": "ss01", "duration_seconds": 35,
                                           "prompt": "Introduce yourself at the start of a job interview."}).json()
    assert ok["lesson_result"]["passed"], ok["lesson_result"]
    assert ok["program"]["done"] == 1 and ok["program"]["today_status"] == "done"   # practising Day 1 enrolled you

    st = client.get("/api/programs").json()[0]
    assert st["enrolled"] and st["streak"] == 1 and _days(st)[1]["status"] == "tomorrow"
    r = client.post("/api/analyze", json={"transcript": INTRO, "lesson_id": "ss02"})
    assert r.status_code == 403 and "tomorrow" in r.json()["detail"]

    lesson_item = next(i for i in client.get("/api/today").json()["items"] if i["key"] == "lesson")
    assert lesson_item["done"] and lesson_item["day"] == 1 and "Day 2 unlocks tomorrow" in lesson_item["detail"]
    assert client.get("/api/today").json()["lessons_done"] == 0      # program days aren't journey lessons

    # the next morning, Day 2 is open and today's lesson slot points at it
    tomorrow = datetime.now().astimezone() + timedelta(days=1)
    monkeypatch.setattr(app_module, "user_now", lambda request: tomorrow)
    st = client.get("/api/programs").json()[0]
    assert _days(st)[1]["status"] == "available" and st["current_day"] == 2 and st["streak"] == 1
    lesson_item = next(i for i in client.get("/api/today").json()["items"] if i["key"] == "lesson")
    assert lesson_item["lesson_id"] == "ss02" and not lesson_item["done"]
    assert client.post("/api/analyze", json={"transcript": INTRO, "lesson_id": "ss02"}).status_code == 200
    detail = client.get("/api/lessons/ss02").json()
    assert detail["program"]["day"] == 2 and detail["index"] == 2 and detail["count"] == 28


def test_program_restart_and_milestones(client):
    client.post("/api/analyze", json={"transcript": INTRO, "lesson_id": "ss01", "duration_seconds": 35})
    st = client.get("/api/programs").json()[0]
    assert st["done"] == 1 and not any(m["reached"] for m in st["milestones"])
    st = client.post("/api/programs/speaking-sprint-28/start").json()       # restart from Day 1
    assert st["enrolled"] and st["done"] == 0 and st["current_day"] == 1
    assert client.post("/api/programs/nope/start").status_code == 404


def test_program_is_per_user(tmp_path, monkeypatch):
    app_module = _fresh_app(tmp_path, monkeypatch)
    ada, bob = TestClient(app_module.app), TestClient(app_module.app)
    _signup(ada, "ada@example.com")
    _signup(bob, "bob@example.com")
    ada.post("/api/analyze", json={"transcript": INTRO, "lesson_id": "ss01", "duration_seconds": 35})
    assert ada.get("/api/programs").json()[0]["done"] == 1
    bob_state = bob.get("/api/programs").json()[0]
    assert bob_state["done"] == 0 and not bob_state["enrolled"]


def test_timezone_header_sets_the_users_day(client):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    r = client.get("/api/today", headers={"X-Timezone": "Pacific/Kiritimati"}).json()     # UTC+14
    assert r["date"] == datetime.now(ZoneInfo("Pacific/Kiritimati")).date().isoformat()
    assert client.get("/api/today", headers={"X-Timezone": "Not/AZone"}).status_code == 200


def test_every_program_day_passes_its_model_answer_and_fails_the_before(monkeypatch):
    """Each day's 'after' example must pass that day's own checks, and the 'before' must fail them —
    with local scoring, so the bar is fair even without an AI key."""
    import re
    from coach import analysis
    from coach.curriculum import Curriculum
    monkeypatch.setenv("LLM_PROVIDER", "local")
    cur = Curriculum()
    clean = lambda t: re.sub(r"\([^)]*\)|\[pause\]|…", "", t)  # noqa: E731  (strip stage directions)
    for lid in cur.program_days["speaking-sprint-28"]:
        les = cur.get(lid)
        ex, t = les["example"], les["exercise"]["target_seconds"]
        run = lambda text: cur.evaluate(les, analysis.analyze(  # noqa: E731
            clean(text), les["exercise"]["mode"], ex["prompt"], False, sum(t) / 2, cur.spec(les)))
        assert run(ex["after"])["passed"], (lid, run(ex["after"]))
        assert not run(ex["before"])["passed"], lid


# ----------------------------------------------------------------------------- v8: three-angle review
def test_local_review_evaluator_coach_and_breakdown():
    from coach import analysis
    a = analysis.analyze(STRONG, "Interview", PROMPT, duration_seconds=40)
    r = a["review"]
    ev = r["evaluator"]
    assert ev["role"] == "Interviewer" and ev["verdict"] == "strong_yes" and ev["verdict_label"] == "Strong yes"
    assert "40 percent" in ev["follow_up"]                              # asks about their own number
    whys = {x["why"] for x in ev["landed"]}
    assert len(whys) == len(ev["landed"]) >= 2                           # varied, specific reasons
    assert all(x["quote"] in STRONG.replace("…", "") or x["quote"].rstrip("…") in STRONG for x in ev["landed"])
    assert [p["label"] for p in r["breakdown"]["parts"]] == ["Situation", "Task", "Action", "Result", "Lesson"]
    assert all(p["status"] == "present" and p["quote"] for p in r["breakdown"]["parts"])

    weak = analysis.analyze(WEAK + ". " + WEAK, "Interview", PROMPT, duration_seconds=30)["review"]
    assert weak["evaluator"]["verdict"] in ("lean_no", "no") and weak["evaluator"]["doubts"]
    quotes = [f["quote"] for f in weak["coach"]["fixes"] if f["quote"]]
    assert len(quotes) == len(set(quotes))                              # never the same sentence twice
    assert weak["coach"]["do_next"] and weak["coach"]["drill"]["steps"]
    assert weak["coach"]["stronger_version"] is None and weak["source"] == "local"


def test_evaluator_role_follows_the_mode():
    from coach import analysis
    roles = {m: analysis.analyze(STRONG, m, "")["review"]["evaluator"]["role"]
             for m in ("Leadership", "Technical Explanation", "Toastmasters", "FDE / Customer")}
    assert roles == {"Leadership": "Leadership team", "Technical Explanation": "Your audience",
                     "Toastmasters": "The audience", "FDE / Customer": "The customer"}


def test_mentor_uses_recent_history(client):
    first = client.post("/api/analyze", json={"transcript": WEAK, "prompt": PROMPT}).json()["analysis"]["review"]
    assert first["mentor"]["based_on"] == 1 and first["mentor"]["note"] and first["mentor"]["habit"]
    for _ in range(2):
        client.post("/api/analyze", json={"transcript": WEAK, "prompt": PROMPT})
    m = client.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT}).json()["analysis"]["review"]["mentor"]
    assert m["based_on"] == 4 and "last 4 takes" in m["pattern"] and m["improving"] and m["habit"]   # includes this take


def test_llm_review_merges_and_challenge_hides_rewrite(monkeypatch):
    from coach import analysis, llm
    fake = {"scores": {"structure": 70, "clarity": 70, "relevance": 70, "answer_quality": 70},
            "review": {"evaluator": {"verdict": "lean_yes", "summary": "Solid, but I want your part.",
                                     "landed": [{"quote": "reduced latency by 40 percent", "why": "Measured."}],
                                     "doubts": [{"quote": None, "why": "No trade-offs."}],
                                     "follow_up": "What did your director say?", "to_strong_yes": ["Lead with it."]},
                       "breakdown": [{"label": "Situation", "status": "present", "quote": "leading the migration",
                                      "note": "Clear."}],
                       "coach": {"fixes": [{"quote": "my director disagreed", "problem": "Late point.",
                                            "fix": "Open with the result."}],
                                 "do_next": ["Open with the 40%."], "drill": {"title": "Headline", "steps": ["a", "b"]},
                                 "stronger_version": "I cut latency 40% by proving my plan with data."},
                       "mentor": {"pattern": "You bury results.", "improving": "Ownership.", "habit": "Lead with results."}}}
    monkeypatch.setattr(llm, "active_provider", lambda: "anthropic")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (json.loads(json.dumps(fake)), "anthropic:test"))
    r = analysis.analyze(STRONG, "Interview", PROMPT)["review"]
    assert r["source"] == "ai" and r["evaluator"]["verdict_label"] == "Lean yes"
    assert r["evaluator"]["follow_up"] == "What did your director say?"
    assert r["coach"]["stronger_version"].startswith("I cut latency") and r["mentor"]["pattern"] == "You bury results."
    assert r["breakdown"]["parts"][0]["note"] == "Clear."
    c = analysis.analyze(STRONG, "Interview", PROMPT, challenge=True)["review"]
    assert c["coach"]["stronger_version"] is None                      # Challenge Mode never hands over an answer

    bad = {"scores": {}, "review": {"evaluator": {"verdict": "amazing"}, "coach": "nonsense"}}
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (bad, "anthropic:test"))
    r = analysis.analyze(STRONG, "Interview", PROMPT)["review"]          # malformed AI output → local values kept
    assert r["evaluator"]["verdict"] in ("strong_yes", "lean_yes", "lean_no", "no") and r["coach"]["fixes"] is not None


def test_ai_prompt_carries_role_and_history(monkeypatch):
    from coach import analysis, llm
    seen = {}

    def capture(system, user, **k):
        seen["system"], seen["user"] = system, user
        return {"scores": {}}, "anthropic:test"
    monkeypatch.setattr(llm, "active_provider", lambda: "anthropic")
    monkeypatch.setattr(llm, "complete_json", capture)
    hist = [{"overall": 55, "structure": 50, "clarity": 60, "relevance": 60, "answer_quality": 50, "delivery": 60,
             "analysis": {"structure_map": [{"label": "Result", "status": "missing"}], "metrics": {"filler_rate": 4}}}] * 3
    analysis.analyze(STRONG, "FDE / Customer", "", history=hist)
    assert "Evaluator: The customer" in seen["user"] and "Recent history (3 takes" in seen["user"]
    assert "Result missing in 3" in seen["user"] and '"review"' in seen["system"]
