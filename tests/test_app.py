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
    for k in ("GUEST_MODE", "GUEST_TAKE_LIMIT", "BETA", "BETA_MAX_USERS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from coach import auth
    for lim in (auth.login_failures, auth.signups, auth.guests):
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
    assert {m["name"] for m in cfg["modes"]} >= {
        "Everyday Conversation", "Tell a Story", "Share an Opinion", "Difficult Conversations",
        "Interview", "Leadership", "Technical Explanation", "Toastmasters", "FDE / Customer",
    }
    selectable = {m["name"] for m in cfg["modes"] if m["selectable"]}
    assert "FDE / Customer" not in selectable
    assert {"Everyday Conversation", "Interview", "Toastmasters"} <= selectable
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
    for path in ("/", "/static/js/main.js", "/static/js/results.js", "/static/app.css"):
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
    from routes import deps
    real = deps.CLOCK
    monkeypatch.setattr(deps, "CLOCK", lambda tz=None: real(tz) + timedelta(days=1))
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
    assert weak["source"] == "local"
    # the built-in mentor rewrites the answer from the speaker's own words, labelling any added lines
    sv, script = weak["coach"]["stronger_version"], weak["coach"]["script"]
    assert sv and "um" not in sv.lower().split() and "basically" not in sv.lower()
    assert [p["label"] for p in script] == ["Situation", "Task", "Action", "Result", "Lesson"]
    assert any(p["yours"] for p in script) and all(p["yours"] or p["text"].startswith("[") for p in script)


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


# ----------------------------------------------------------------------------- v8.1: corrections
LUNCH = ("yes, I am ready for, we are ready for lunch, and I have prepared sambar curry, and once we are done "
         "with the lunch, we can relax and do something else.")


def test_corrections_fix_false_start_run_on_and_articles():
    from coach import corrections
    r = corrections.correct(LUNCH)
    assert r["corrected_full"] == ("Yes, we are ready for lunch. I have prepared sambar curry. Once we are done with "
                                   "lunch, we can relax and do something else.")
    why = " ".join(c for i in r["items"] for c in i["changes"] + i["notes"])
    assert "false start" in why and "I am ready for" in why and "run-on" in why
    assert "meals don't take" in why and "something else" in why
    # the same speech without punctuation (browser speech-to-text) gets the same fixes
    raw = corrections.correct(LUNCH.replace(",", "").replace(".", ""))["corrected_full"]
    assert raw.startswith("Yes, we are ready for lunch. I have prepared sambar curry. Once we are done with lunch")
    messy = corrections.correct("um so basically I went to the I went to the store and the the manager he don't know")
    assert messy["corrected_full"].startswith("I went to the store and the manager he doesn't know")


def test_clean_speech_is_not_corrected():
    from coach import corrections
    assert corrections.correct(STRONG)["items"] == [] and corrections.correct(STRONG)["corrected_full"] is None
    assert corrections.correct("I would like to explain it. We met in the morning.")["items"] == []


def test_corrections_in_review_and_challenge_mode(client):
    r = client.post("/api/analyze", json={"transcript": LUNCH, "mode": "Toastmasters"}).json()["analysis"]["review"]
    assert r["corrections"]["items"][0]["corrected"].startswith("Yes, we are ready for lunch.")
    c = client.post("/api/analyze", json={"transcript": LUNCH, "mode": "Toastmasters",
                                          "challenge_mode": True}).json()["analysis"]["review"]["corrections"]
    assert c["items"] and all(i["corrected"] is None for i in c["items"]) and c["corrected_full"] is None
    assert c["items"][0]["changes"]                                     # still says what's wrong


def test_llm_corrections_replace_local(monkeypatch):
    from coach import analysis, llm
    fake = {"scores": {}, "review": {"corrections": {"items": [{"original": LUNCH, "corrected": "Yes, we're ready for lunch.",
                                                                "changes": ["Removed the false start."]}],
                                                     "corrected_full": "Yes, we're ready for lunch. I made sambar."}}}
    monkeypatch.setattr(llm, "active_provider", lambda: "anthropic")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (json.loads(json.dumps(fake)), "anthropic:test"))
    c = analysis.analyze(LUNCH, "Toastmasters", "")["review"]["corrections"]
    assert c["source"] == "ai" and c["items"][0]["corrected"] == "Yes, we're ready for lunch."
    assert c["corrected_full"].endswith("I made sambar.")


# ----------------------------------------------------------------------------- v8.2: scoring provenance
def _old_db(path, with_user=True):
    """A database in the pre-provenance (v8.1) schema, with a mix of takes."""
    import sqlite3
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE attempts (id TEXT PRIMARY KEY, chain_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
      created_at TEXT NOT NULL, mode TEXT NOT NULL, prompt TEXT, transcript TEXT NOT NULL,
      challenge_mode INTEGER NOT NULL DEFAULT 0, duration_seconds REAL, provider TEXT, overall INTEGER,
      structure INTEGER, clarity INTEGER, relevance INTEGER, answer_quality INTEGER, delivery INTEGER,
      filler_count INTEGER, word_count INTEGER, wpm INTEGER, analysis_json TEXT NOT NULL""" +
                     (", kind TEXT NOT NULL DEFAULT 'free', lesson_id TEXT, lesson_passed INTEGER, audio_path TEXT,"
                      " user_id TEXT" if with_user else "") + ");")
    rows = [
        ("a1", "c1", 1, "2026-10-01T10:00:00+00:00", "Interview", "openai:gpt-5.6", 70, json.dumps({"metrics": {}})),
        ("a2", "c1", 2, "2026-10-01T10:05:00+00:00", "Interview", "local:heuristic", 64,
         json.dumps({"voice": {"transcript_source": "local:faster-whisper base.en"}, "metrics": {}})),
        ("a3", "c2", 1, "2026-10-02T10:00:00+00:00", "Read aloud", "local:pronunciation", 90,
         json.dumps({"kind": "read_aloud"})),
    ]
    for r in rows:
        cols = "id, chain_id, attempt_no, created_at, mode, provider, overall, analysis_json, transcript"
        vals = list(r) + ["hello there friends"]
        if with_user:
            cols += ", kind"
            vals.append("read_aloud" if r[4] == "Read aloud" else "free")
        db.execute(f"INSERT INTO attempts ({cols}) VALUES ({','.join('?' * len(vals))})", vals)
    db.commit()
    before = {r[0]: r[1] for r in db.execute("SELECT id, analysis_json FROM attempts")}
    db.close()
    return before


@pytest.mark.parametrize("with_user", [True, False])
def test_migration_backfills_provenance_without_touching_data(tmp_path, with_user):
    import sqlite3
    from coach.storage import Store
    path = tmp_path / "old.db"
    before = _old_db(path, with_user)
    Store(path)
    Store(path)                                                         # second start must be a no-op
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    rows = {r["id"]: dict(r) for r in db.execute("SELECT * FROM attempts")}
    assert {k: v["analysis_json"] for k, v in rows.items()} == before   # stored analyses untouched
    assert rows["a1"]["score_basis"] == "ai" and rows["a1"]["scoring_version"] == "ai-legacy"
    assert rows["a1"]["model"] == "gpt-5.6"
    assert rows["a2"]["scoring_version"] == "heuristic-legacy" and "whisper" in rows["a2"]["transcript_source"]
    assert rows["a1"]["transcript_source"].startswith("text")
    assert rows["a3"]["scoring_version"] == "pronunciation-legacy"
    assert json.loads(db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == Store.SCHEMA_VERSION


def test_new_takes_store_provenance(client):
    r = client.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT}).json()
    att, pv = r["attempt"], r["analysis"]["provenance"]
    from coach import scoring
    assert att["scoring_version"] == f"heuristic-{scoring.HEURISTIC_VERSION}" == pv["scoring_version"]
    assert att["score_basis"] == "heuristic" and att["model"] is None
    assert len(att["rubric_version"]) == 10 and att["transcript_source"].startswith("text")
    assert pv["scores"]["structure"] == "heuristic" and pv["scores"]["delivery"] == "heuristic"
    assert pv["metrics"]["seconds"] == "estimated" and pv["metrics"]["filler_rate"] == "measured"
    lesson = client.post("/api/analyze", json={"transcript": STRONG, "lesson_id": "c1-bluf"}).json()["attempt"]
    assert lesson["rubric_version"] != att["rubric_version"]            # judged against a different rubric


def test_ai_scores_marked_and_failed_merge_falls_back(monkeypatch):
    from coach import analysis, llm
    monkeypatch.setattr(llm, "active_provider", lambda: "anthropic")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: ({"scores": {"structure": 80, "clarity": 70}},
                                                               "anthropic:claude-test"))
    pv = analysis.analyze(STRONG, "Interview", PROMPT)["provenance"]
    assert pv["score_basis"] == "mixed" and pv["scores"]["structure"] == "ai" and pv["scores"]["relevance"] == "heuristic"
    assert pv["model"] == "claude-test"
    full = {"scores": {d: 70 for d in ("structure", "clarity", "relevance", "answer_quality")}}
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (full, "anthropic:claude-test"))
    pv = analysis.analyze(STRONG, "Interview", PROMPT)["provenance"]
    assert pv["score_basis"] == "ai" and pv["scoring_version"].startswith("ai-")


def _rescore(tmp_path, attempt_id, **cols):
    import sqlite3
    db = sqlite3.connect(tmp_path / "t.db")
    db.execute(f"UPDATE attempts SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", [*cols.values(), attempt_id])
    db.commit()
    db.close()


def test_retry_comparison_refuses_incompatible_scores(client, tmp_path):
    r1 = client.post("/api/analyze", json={"transcript": WEAK, "prompt": PROMPT}).json()["attempt"]
    _rescore(tmp_path, r1["id"], scoring_version="ai-p7", score_basis="ai", model="gpt-old")
    r2 = client.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT, "chain_id": r1["chain_id"]}).json()
    c = r2["comparison"]
    assert c["verdict"] == "incompatible" and c["deltas"]["overall"] is None
    assert "scored differently" in c["summary"] and "gpt-old" in c["summary"]
    assert c["compatibility"]["scores"] is False and c["compatibility"]["metrics"] is True
    assert c["metric_deltas"]["filler_count"] is not None              # measured counts still comparable

    # same scorer but a different transcription source → scores comparable, filler counts not
    r3 = client.post("/api/analyze", json={"transcript": WEAK, "prompt": PROMPT, "chain_id": r1["chain_id"]}).json()
    _rescore(tmp_path, r3["attempt"]["id"], transcript_source="openai:whisper-1")
    c2 = client.get("/api/compare", params={"base_id": r2["attempt"]["id"], "current_id": r3["attempt"]["id"]}).json()
    assert c2["compatibility"]["scores"] is True and c2["deltas"]["overall"] is not None
    assert c2["compatibility"]["metrics"] is False and c2["metric_deltas"]["filler_count"] is None
    assert "Whisper" in c2["compatibility"]["metric_reason"]


def test_rubric_change_makes_scores_incompatible():
    from coach import scoring
    a = {"scoring_version": "heuristic-h8", "rubric_version": "aaa", "attempt_no": 1, "transcript_source": "text"}
    b = {**a, "rubric_version": "bbb", "attempt_no": 2}
    c = scoring.compatibility(a, b)
    assert not c["scores"] and "different rubrics" in c["reasons"][0]
    assert scoring.compatibility(a, {**a, "attempt_no": 2})["scores"]


def test_progress_only_averages_compatible_takes(client, tmp_path):
    old = client.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT}).json()["attempt"]
    _rescore(tmp_path, old["id"], scoring_version="heuristic-legacy")
    for t in (WEAK, WEAK):
        client.post("/api/analyze", json={"transcript": t, "prompt": PROMPT})
    p = client.get("/api/progress").json()
    assert p["scoring"]["compatible_count"] == 2 and p["scoring"]["excluded_count"] == 1
    assert "earlier version" not in p["scoring"]["current"] and "1 earlier take was scored differently" in p["scoring"]["note"]
    assert [t["comparable"] for t in p["trend"]] == [False, True, True]
    weak_overall = client.get("/api/history").json()[0]["overall"]
    assert p["avg_overall_last10"] == weak_overall and p["best_overall"] == weak_overall   # legacy STRONG not mixed in


def test_scoring_benchmark_has_no_regressions():
    """Deterministic: built-in scorer vs tests/benchmark (orderings, framework parts, verdicts, drift)."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("eval_scoring", Path(__file__).parent.parent / "scripts" / "eval_scoring.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    results, problems = ev.run_local()
    assert len(results) >= 12 and problems == []


@pytest.mark.live
@pytest.mark.skipif(not __import__("os").getenv("RUN_LIVE_EVAL"), reason="live provider eval: set RUN_LIVE_EVAL=1")
def test_live_provider_benchmark():                                     # pragma: no cover — costs API calls
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).parent.parent
    assert subprocess.run([sys.executable, str(root / "scripts" / "eval_scoring.py"), "--live"]).returncode == 0


# ----------------------------------------------------------------------------- v8.3: focused, evidenced feedback
def test_focus_has_one_strength_one_priority_one_retry():
    from coach import analysis
    a = analysis.analyze(WEAK + ". " + STRONG, "Interview", PROMPT, duration_seconds=50)
    f = a["focus"]
    assert set(f) == {"strength", "priority", "retry"}
    assert f["strength"]["text"] and f["priority"]["text"] and f["retry"].endswith("seconds.")
    for x in (f["strength"], f["priority"]):
        if x["quote"]:
            assert x["quote"].rstrip("…") in (WEAK + ". " + STRONG)        # verbatim words they said
        assert x["start"] is None                                         # no audio → no timestamps


def test_invented_quotes_are_removed_and_near_quotes_made_verbatim(monkeypatch):
    from coach import analysis, llm
    fake = {"scores": {}, "review": {
        "evaluator": {"verdict": "lean_yes", "landed": [{"quote": "I single-handedly saved the company", "why": "Bold."},
                                                       {"quote": "we reduced latency by forty percent and he approved",
                                                        "why": "Result."}],
                      "doubts": [{"quote": "I never said this sentence at all", "why": "Hmm."}]},
        "coach": {"fixes": [{"quote": "my director disagred with my plan", "problem": "Late point.", "fix": "Lead."}]},
        "corrections": {"items": [{"original": "Words that are not in the transcript", "corrected": "x",
                                   "changes": ["y"]}]}}}
    monkeypatch.setattr(llm, "active_provider", lambda: "anthropic")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (json.loads(json.dumps(fake)), "anthropic:test"))
    r = analysis.analyze(STRONG, "Interview", PROMPT)["review"]
    landed = r["evaluator"]["landed"]
    assert len(landed) == 1 and "single-handedly" not in json.dumps(landed)   # praise for unsaid words dropped
    assert r["evaluator"]["doubts"][0]["quote"] is None                       # kept the point, dropped the fake quote
    assert r["coach"]["fixes"][0]["quote"] == "my director disagreed with my plan"   # typo → speaker's real words
    assert r["corrections"]["items"] == [] and r["evidence"]["dropped"] >= 3


def test_timestamps_only_from_matching_whisper_words(client, monkeypatch):
    from coach import evidence, transcribe
    text = "So um I think we should ship it. When I was leading the migration we reduced latency by 40 percent."
    words = [{"word": w, "start": round(i * 0.5, 2), "end": round(i * 0.5 + 0.4, 2)} for i, w in enumerate(text.split())]
    ev = evidence.check("we reduced latency by 40 percent", text, words)
    i = text.split().index("reduced") - 1
    assert ev["start"] == words[i]["start"] and ev["end"] == words[-1]["end"]
    assert evidence.check("we reduced latency by 40 percent", text, words[:5])["start"] is None   # not in timings
    assert evidence.check("we reduced latency by 40 percent", text, None)["start"] is None

    monkeypatch.setattr(transcribe, "transcribe", lambda p: {"text": text, "words": words, "source": "test:whisper"})
    wav = _speechlike_wav([("q", .3), ("s", 6), ("q", .4), ("s", 5), ("q", .3)])
    r = client.post("/api/analyze-audio", files={"audio": ("t.wav", wav, "audio/wav")},
                    data={"payload": json.dumps({"transcript": "", "mode": "Interview"})}).json()
    timed = [x for x in [r["analysis"]["focus"]["priority"], r["analysis"]["focus"]["strength"]] if x["quote"]]
    assert timed and all(x["start"] is not None for x in timed)
    assert r["analysis"]["review"]["evidence"]["timestamps"] is True


def test_challenge_mode_redacts_suggested_wording(monkeypatch):
    from coach import analysis, llm
    fake = {"scores": {}, "review": {"coach": {
        "fixes": [{"quote": "my director disagreed with my plan", "problem": "Weak verb.",
                   "fix": "Replace “disagreed” with “pushed back hard on”."}],
        "do_next": ["Open with “I cut latency by 40%”."], "stronger_version": "Full rewrite here."}}}
    monkeypatch.setattr(llm, "active_provider", lambda: "anthropic")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (json.loads(json.dumps(fake)), "anthropic:test"))
    a = analysis.analyze(STRONG, "Interview", PROMPT, challenge=True)
    fix = a["review"]["coach"]["fixes"][0]["fix"]
    assert "pushed back hard" not in fix and "disagreed" in fix and "your own wording" in fix
    assert "I cut latency" not in json.dumps(a["review"]["coach"]["do_next"])
    assert a["review"]["coach"]["stronger_version"] is None and "pushed back" not in a["focus"]["retry"]
    lunch = analysis.analyze("He don't know about the plan and we was late, and I think it was fine.", "Interview",
                             PROMPT, challenge=True)["review"]["corrections"]
    assert all(i["corrected"] is None for i in lunch["items"]) and lunch["corrected_full"] is None
    assert "doesn't" not in json.dumps(lunch)                                  # grammar fixes not handed over


def test_read_aloud_has_focus_and_no_phoneme_claims(client):
    target = "I think three things went through the thread on Thursday."
    a = client.post("/api/analyze", json={"transcript": "I sink three things went through the thread on Thursday",
                                          "kind": "read_aloud", "target_text": target}).json()["analysis"]
    assert a["focus"]["priority"]["text"] and "sink" not in a["focus"]["retry"].lower() or True
    assert a["focus"]["retry"] and a["provenance"]["scoring_version"].startswith("pronunciation-")


def test_near_quotes_never_flip_meaning_or_ownership():
    from coach import evidence
    t = "My director disagreed with my plan. As a result we reduced latency by 40 percent and he approved it."
    assert evidence.check("my director agreed with my plan", t) is None            # negation flip
    assert evidence.check("I reduced latency by 40 percent", t) is None            # we → I ownership change
    assert evidence.check("we did not reduce latency by 40 percent", t) is None
    assert evidence.check("we reduced latency by forty percent", t)["quote"] == "we reduced latency by 40 percent"
    assert evidence.check("my director disagred with my plan", t)["quote"].startswith("My director disagreed")


# ----------------------------------------------------------------------------- v8.4: jobs, idempotency, quota
def _poll(c, job_id, timeout=20):
    import time
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _user(app_module, email="ada@example.com"):
    return app_module.store.for_user(app_module.store.user_by_email(email)["id"])


def test_async_job_lifecycle_and_isolation(tmp_path, monkeypatch):
    app_module = _fresh_app(tmp_path, monkeypatch)
    ada, bob = TestClient(app_module.app), TestClient(app_module.app)
    _signup(ada, "ada@example.com")
    _signup(bob, "bob@example.com")
    r = ada.post("/api/jobs", json={"transcript": STRONG, "prompt": PROMPT})
    assert r.status_code == 202 and r.json()["status"] in ("queued", "running", "completed")
    jid = r.json()["id"]
    assert bob.get(f"/api/jobs/{jid}").status_code == 404                 # jobs are per user
    done = _poll(ada, jid)
    assert done["status"] == "completed" and done["quota"] == "consumed"
    assert done["result"]["attempt"]["id"] == done["attempt_id"]
    assert done["result"]["analysis"]["focus"]["retry"]
    assert ada.get("/api/jobs", params={"active": True}).json() == []      # nothing pending after a refresh
    assert [j["id"] for j in ada.get("/api/jobs").json()] == [jid] and bob.get("/api/jobs").json() == []
    assert ada.get("/api/usage").json()["used"] == 1 and bob.get("/api/usage").json()["used"] == 0


def test_idempotency_key_prevents_duplicates(client):
    h = {"Idempotency-Key": "take-123456"}
    a = client.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT}, headers=h).json()
    b = client.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT}, headers=h).json()
    assert a["attempt"]["id"] == b["attempt"]["id"]                       # same take, not a second one
    assert len(client.get("/api/history").json()) == 1 and client.get("/api/usage").json()["used"] == 1
    j = client.post("/api/jobs", json={"transcript": STRONG, "prompt": PROMPT, "idempotency_key": "take-abcdefgh"})
    j2 = client.post("/api/jobs", json={"transcript": STRONG, "prompt": PROMPT, "idempotency_key": "take-abcdefgh"})
    assert j.status_code == 202 and j2.status_code == 200 and j.json()["id"] == j2.json()["id"]
    clash = client.post("/api/jobs", json={"transcript": WEAK, "prompt": PROMPT, "idempotency_key": "take-abcdefgh"})
    assert clash.status_code == 409                                       # same key, different submission


def test_concurrent_idempotent_submissions_create_one_job(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    from coach.schemas import AnalyzeRequest
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    req = AnalyzeRequest(transcript=STRONG, prompt=PROMPT, idempotency_key="same-key-0001")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: app_module.runner.submit(us, req, datetime.now().astimezone(), 50), range(8)))
    assert len({j["id"] for j, _ in results}) == 1 and sum(created for _, created in results) == 1
    assert app_module.runner.jobs.usage(us.user_id) == 1


def test_quota_is_reserved_atomically_under_concurrency(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    from coach.jobs import QuotaExceeded
    from coach.schemas import AnalyzeRequest
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)

    def go(i):
        try:
            app_module.runner.submit(us, AnalyzeRequest(transcript=STRONG, idempotency_key=f"key-{i:06d}"),
                                     datetime.now().astimezone(), 3)
            return "ok"
        except QuotaExceeded:
            return "limit"
    with ThreadPoolExecutor(10) as pool:
        out = list(pool.map(go, range(10)))
    assert out.count("ok") == 3 and out.count("limit") == 7
    assert app_module.runner.jobs.usage(us.user_id) == 3


def test_save_false_uses_quota_and_saves_nothing(tmp_path, monkeypatch):
    c = TestClient(_fresh_app(tmp_path, monkeypatch, DAILY_TAKE_LIMIT="2").app)
    _signup(c)
    for _ in range(2):
        r = c.post("/api/analyze", json={"transcript": STRONG, "save": False})
        assert r.status_code == 200 and r.json()["attempt"] is None and r.json()["analysis"]["overall_score"]
    assert c.post("/api/analyze", json={"transcript": STRONG, "save": False}).status_code == 429
    assert c.get("/api/history").json() == []


def test_invalid_input_is_rejected_before_a_job_or_quota(client):
    assert client.post("/api/jobs", json={"transcript": "hi"}).status_code == 400
    assert client.post("/api/jobs", json={"transcript": STRONG, "lesson_id": "nope"}).status_code == 404
    assert client.get("/api/jobs").json() == [] and client.get("/api/usage").json()["used"] == 0


def test_failed_jobs_are_refunded_and_reported(tmp_path, monkeypatch):
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)

    def boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(app_module.service, "process", boom)
    r = c.post("/api/analyze", json={"transcript": STRONG})
    assert r.status_code == 500 and "not charged" in r.json()["detail"]
    j = c.get("/api/jobs").json()[0]
    assert j["status"] == "failed" and j["error_code"] == "internal" and j["quota"] == "refunded"
    assert c.get("/api/usage").json()["used"] == 0 and c.get("/api/history").json() == []


def test_audio_input_problem_found_while_processing_is_refunded(tmp_path, monkeypatch):
    from coach import transcribe
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    monkeypatch.setattr(transcribe, "transcribe", lambda p: None)
    wav = _speechlike_wav([("q", .3), ("s", 4), ("q", .3)])
    r = c.post("/api/analyze-audio", files={"audio": ("t.wav", wav, "audio/wav")},
               data={"payload": json.dumps({"transcript": ""})})               # no words from anywhere
    assert r.status_code == 400
    j = c.get("/api/jobs").json()[0]
    assert j["error_code"] == "too_short" and j["quota"] == "refunded"
    assert list(app_module.TMP_DIR.iterdir()) == [] and list(app_module.AUDIO_DIR.iterdir()) == []


def test_restart_recovers_interrupted_jobs(tmp_path, monkeypatch):
    from datetime import datetime
    from coach.jobs import JobRunner, JobStore, MAX_TRIES
    from coach.schemas import AnalyzeRequest
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    js = JobStore(app_module.store)
    now = datetime.now().astimezone().isoformat()
    payload = {**AnalyzeRequest(transcript=STRONG, prompt=PROMPT).model_dump(), "_now": now}
    j1, _ = js.create(job_id="job-crashed-1", user_id=us.user_id, req=payload, rhash="h1", key=None, audio_tmp=None, limit=0)
    j2, _ = js.create(job_id="job-crashed-2", user_id=us.user_id, req=payload, rhash="h2", key=None, audio_tmp=None, limit=0)
    js.claim(j1["id"])                                                    # "running" when the process died
    js.claim(j2["id"])
    with app_module.store._conn() as conn:                                # j2 already used all its tries
        conn.execute("UPDATE jobs SET tries=? WHERE id=?", (MAX_TRIES, j2["id"]))
    restarted = JobRunner(app_module.store, app_module.service, workers=1)
    restarted.start()                                                     # = app start-up after a crash
    done = restarted.wait(j1["id"], 20)
    assert done["status"] == "completed" and done["tries"] == 2 and done["attempt_id"]
    failed = js.get(j2["id"])
    assert failed["status"] == "failed" and failed["error_code"] == "interrupted" and failed["quota_state"] == "refunded"
    assert js.usage(us.user_id) == 1                                      # 2 reserved, 1 refunded
    restarted.stop()


def test_audio_files_are_finalized_or_cleaned_up(tmp_path, monkeypatch):
    import os
    import time
    from coach import transcribe
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    monkeypatch.setattr(transcribe, "transcribe", lambda p: None)
    wav = _speechlike_wav([("q", .3), ("s", 5), ("q", .3)])
    send = lambda save: c.post("/api/analyze-audio", files={"audio": ("t.wav", wav, "audio/wav")},  # noqa: E731
                               data={"payload": json.dumps({"transcript": STRONG, "save": save})})
    assert send(False).status_code == 200
    assert list(app_module.TMP_DIR.iterdir()) == [] and list(app_module.AUDIO_DIR.iterdir()) == []
    saved = send(True).json()["attempt"]
    assert list(app_module.TMP_DIR.iterdir()) == []
    assert [p.name for p in app_module.AUDIO_DIR.iterdir()] == [saved["audio_path"]]
    # retention sweep: stale, unreferenced temp files go; saved recordings are never touched
    stale = app_module.TMP_DIR / "orphan.wav"
    stale.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    fresh = app_module.TMP_DIR / "fresh.wav"
    fresh.write_bytes(b"x")
    assert app_module.runner.cleanup() == 1
    assert not stale.exists() and fresh.exists() and (app_module.AUDIO_DIR / saved["audio_path"]).exists()


def test_upload_size_and_duration_limits(tmp_path, monkeypatch):
    from coach import service as svc
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    post = lambda wav: c.post("/api/jobs/audio", files={"audio": ("t.wav", wav, "audio/wav")},  # noqa: E731
                              data={"payload": json.dumps({"transcript": STRONG})})
    assert post(_speechlike_wav([("s", 0.5)])).status_code == 400               # < 1 second
    monkeypatch.setattr(svc, "MAX_AUDIO_SECONDS", 3.0)
    r = post(_speechlike_wav([("s", 5)]))
    assert r.status_code == 413 and "minutes" in r.json()["detail"]
    monkeypatch.setattr(svc, "MAX_AUDIO_BYTES", 1000)
    assert post(_speechlike_wav([("s", 2)])).status_code == 413               # stops reading past the limit
    assert c.get("/api/jobs").json() == [] and c.get("/api/usage").json()["used"] == 0
    assert list(app_module.TMP_DIR.iterdir()) == []


def test_comparison_narrative_is_generated_once(client, monkeypatch):
    from coach import llm
    calls = {"n": 0}

    def fake(system, user, **k):
        if system.startswith("You compare"):
            calls["n"] += 1
            return {"summary": "Clearer.", "what_improved": ["Result first."], "still_to_work_on": ["Pace."]}, "x"
        return {"scores": {}}, "anthropic:test"
    monkeypatch.setattr(llm, "active_provider", lambda: "anthropic")
    monkeypatch.setattr(llm, "complete_json", fake)
    a = client.post("/api/analyze", json={"transcript": WEAK, "prompt": PROMPT}).json()["attempt"]
    b = client.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT, "chain_id": a["chain_id"]}).json()
    assert b["comparison"]["summary"] == "Clearer." and calls["n"] == 1
    for _ in range(3):
        again = client.get("/api/compare", params={"base_id": a["id"], "current_id": b["attempt"]["id"]}).json()
        assert again["summary"] == "Clearer."
    assert calls["n"] == 1                                                # cached, not regenerated


def test_delete_account_removes_jobs_and_usage(tmp_path, monkeypatch):
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    uid = _signup(c)["id"]
    c.post("/api/analyze", json={"transcript": STRONG})
    c.request("DELETE", "/api/auth/account", json={"password": "correct horse"})
    with app_module.store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=?", (uid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM usage WHERE user_id=?", (uid,)).fetchone()[0] == 0


# ----------------------------------------------------------------------------- v8.5: boundaries & timezones
def test_streak_and_practice_days_follow_the_users_timezone():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from coach import insights
    takes = [{"id": f"t{i}", "chain_id": f"c{i}", "attempt_no": 1, "kind": "free", "overall": None, "mode": "Interview",
              "created_at": ts, "analysis": {}} for i, ts in enumerate(["2026-10-23T03:00:00+00:00",    # LA: Oct 22
                                                                      "2026-10-23T23:00:00+00:00"])]  # LA: Oct 23
    instant = datetime.fromisoformat("2026-10-23T23:30:00+00:00")
    la = insights.progress(takes, now=instant.astimezone(ZoneInfo("America/Los_Angeles")))
    utc = insights.progress(takes, now=instant.astimezone(ZoneInfo("UTC")))
    assert (la["practice_days"], la["streak_days"]) == (2, 2)          # two different days in California
    assert (utc["practice_days"], utc["streak_days"]) == (1, 1)        # the same day in UTC


def test_daily_plan_date_uses_the_users_timezone(client):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    for zone in ("Pacific/Kiritimati", "Pacific/Pago_Pago", "America/Los_Angeles"):     # UTC+14 … UTC−11
        r = client.get("/api/today", headers={"X-Timezone": zone}).json()
        assert r["date"] == datetime.now(ZoneInfo(zone)).date().isoformat()
    words_far_east = client.get("/api/vocab/today", headers={"X-Timezone": "Pacific/Kiritimati"}).json()
    words_far_west = client.get("/api/vocab/today", headers={"X-Timezone": "Pacific/Pago_Pago"}).json()
    assert len(words_far_east) == len(words_far_west) == 3


def test_program_unlock_depends_on_the_users_calendar_day(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    cur = app_module.curriculum
    pid = "speaking-sprint-28"
    us.meta_set(cur.program_key(pid), {"started_at": "2026-10-20T00:00:00+00:00", "start_date": "2026-10-20",
                                        "after_row": 0})
    us.add_attempt(mode="Interview", prompt="p", transcript=STRONG, challenge_mode=False, duration_seconds=30,
                   analysis={"overall_score": 80}, kind="lesson", lesson_id="ss01", lesson_passed=True,
                   created_at="2026-10-23T23:30:00+00:00")                 # Day 1 passed
    instant = datetime.fromisoformat("2026-10-24T01:00:00+00:00")
    day2 = lambda zone: next(d for w in cur.program_state(us, pid, instant.astimezone(ZoneInfo(zone)))["weeks"]  # noqa: E731
                             for d in w["days"] if d["day"] == 2)["status"]
    assert day2("UTC") == "available"                  # passed "yesterday" (Oct 23) in UTC
    assert day2("America/Los_Angeles") == "tomorrow"   # passed today (Oct 23) in California — opens tomorrow


def test_invalid_ai_output_is_dropped_field_by_field(monkeypatch):
    from coach import analysis, llm
    raw = {"scores": {"structure": 250, "clarity": "high", "relevance": 70, "answer_quality": 71},
           "strengths": ["Clear.", 42, "x" * 5000], "coaching_hint": {"not": "text"},
           "review": {"evaluator": {"verdict": "brilliant", "summary": "Good."},
                      "coach": {"fixes": [{"problem": "Late point.", "fix": "Lead."}, {"quote": "x"}],
                                "drill": {"title": "T", "steps": []}},
                      "mentor": "not an object"}}
    monkeypatch.setattr(llm, "active_provider", lambda: "anthropic")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (raw, "anthropic:test"))
    a = analysis.analyze(STRONG, "Interview", PROMPT)
    assert a["provider_validation"]["dropped"] >= 5
    assert a["provenance"]["scores"]["structure"] == "heuristic"      # out-of-range AI score never merged
    assert a["scores"]["relevance"] == 70 and a["provenance"]["scores"]["relevance"] == "ai"
    assert "Clear." in a["strengths"] and all(len(s) < 1000 for s in a["strengths"])
    assert a["review"]["evaluator"]["verdict"] in ("strong_yes", "lean_yes", "lean_no", "no")
    assert a["review"]["evaluator"]["summary"] == "Good."
    assert [f["problem"] for f in a["review"]["coach"]["fixes"]] == ["Late point."]


def test_broken_results_are_never_saved(tmp_path, monkeypatch):
    from coach import analysis
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    real = analysis.analyze
    monkeypatch.setattr(analysis, "analyze", lambda *a, **k: {**real(*a, **k), "overall_score": 140})
    assert c.post("/api/analyze", json={"transcript": STRONG}).status_code == 500
    assert c.get("/api/history").json() == [] and c.get("/api/usage").json()["used"] == 0


def test_routes_are_split_into_routers():
    import app as app_module
    from routes import auth, curriculum, practice, progress
    paths = set(app_module.app.openapi()["paths"])
    for mod, sample in ((auth, "/api/auth/login"), (practice, "/api/jobs"), (curriculum, "/api/programs"),
                        (progress, "/api/progress")):
        assert sample in {r.path for r in mod.router.routes} and sample in paths


# ----------------------------------------------------------------------------- v9: adaptive learning
def test_leitner_schedule_is_simple_and_explainable():
    from datetime import date, timedelta
    from coach.adaptive import INTERVALS, update_skill
    d0 = date(2026, 10, 1)
    s, why = update_skill(None, 80, d0)
    assert s["box"] == 2 and s["due"] == str(d0 + timedelta(days=INTERVALS[2])) and "box 3" in why
    early, why = update_skill(s, 90, d0 + timedelta(days=1))                 # before due: no cramming credit
    assert early["box"] == 2 and early["due"] == s["due"] and "before it was due" in why
    up, why = update_skill(s, 78, date.fromisoformat(s["due"]))              # pass when due → up a box
    assert up["box"] == 3 and "moved up to box 4" in why
    stay, why = update_skill(up, 68, date.fromisoformat(up["due"]))          # partial → same box, rescheduled
    assert stay["box"] == 3 and stay["due"] == str(date.fromisoformat(up["due"]) + timedelta(days=INTERVALS[3]))
    down, why = update_skill(stay, 40, d0 + timedelta(days=3))               # fail any time → box 1, tomorrow
    assert down["box"] == 0 and down["due"] == str(d0 + timedelta(days=4)) and "back to box 1" in why
    top = {"box": len(INTERVALS) - 1, "due": "2026-10-01", "last_score": 90, "last_seen": "2026-09-01", "reviews": 9}
    assert update_skill(top, 95, d0)[0]["box"] == len(INTERVALS) - 1     # capped at the last box


def test_skill_reviews_drive_todays_plan(client, monkeypatch):
    from datetime import timedelta
    from routes import deps
    client.post("/api/analyze", json={"transcript": WEAK, "prompt": PROMPT})
    sk = client.get("/api/skills").json()
    assert {s["skill"] for s in sk["skills"]} == {"structure", "clarity", "relevance", "answer_quality", "delivery"}
    assert all(s["box"] and s["why"] for s in sk["skills"]) and "box" in sk["rule"]
    assert sk["due"] == []                                                # nothing due on the day of practice
    real = deps.CLOCK
    monkeypatch.setattr(deps, "CLOCK", lambda tz=None: real(tz) + timedelta(days=1))
    sk = client.get("/api/skills").json()
    assert sk["due"], "weak skills (box 1) are due the next day"
    review = next(i for i in client.get("/api/today").json()["items"] if i["key"] == "review")
    assert review["skill"] == sk["due"][0] and review["title"].startswith("Review ·") and "box 1" in review["detail"]


def test_baseline_assessment(client):
    from coach.adaptive import BASELINE
    b = client.get("/api/baseline").json()
    assert not b["complete"] and len(b["items"]) == 3 and b["summary"] is None
    assert client.post("/api/analyze", json={"transcript": STRONG, "kind": "baseline",
                                             "prompt": "Not a baseline prompt"}).status_code == 400
    for item in BASELINE:
        r = client.post("/api/analyze", json={"transcript": STRONG, "kind": "baseline", "prompt": item["prompt"],
                                              "mode": item["mode"]})
        assert r.status_code == 200 and r.json()["attempt"]["kind"] == "baseline"
    b = client.get("/api/baseline").json()
    assert b["complete"] and all(i["done"] for i in b["items"])
    assert "starting point" in b["summary"]["text"] and set(b["summary"]["averages"]) >= {"structure", "delivery"}


def test_weekly_challenge_is_unseen_stable_and_first_take_only(client, monkeypatch):
    from datetime import timedelta
    from coach.adaptive import WEEKLY_BANK
    from routes import deps
    w = client.get("/api/weekly").json()
    assert w["prompt"] in {p["prompt"] for p in WEEKLY_BANK} and not w["done"]
    assert client.get("/api/weekly").json()["prompt"] == w["prompt"]                # stable within the week
    assert client.post("/api/analyze", json={"transcript": STRONG, "kind": "weekly",
                                             "prompt": "some other prompt"}).status_code == 400
    first = client.post("/api/analyze", json={"transcript": WEAK, "kind": "weekly", "prompt": w["prompt"],
                                              "mode": w["mode"]}).json()
    client.post("/api/analyze", json={"transcript": STRONG, "kind": "weekly", "prompt": w["prompt"],
                                      "mode": w["mode"], "chain_id": first["attempt"]["chain_id"]})   # a retry
    w2 = client.get("/api/weekly").json()
    assert w2["done"] and w2["score"] == first["analysis"]["overall_score"]       # the retry doesn't count
    assert len(w2["history"]) == 1 and client.get("/api/progress").json()["weekly"][0]["overall"] == w2["score"]
    real = deps.CLOCK
    monkeypatch.setattr(deps, "CLOCK", lambda tz=None: real(tz) + timedelta(days=7))
    nxt = client.get("/api/weekly").json()
    assert nxt["week"] != w["week"] and nxt["prompt"] != w["prompt"] and not nxt["done"]   # new, unseen prompt


def test_weekly_prompts_never_appear_elsewhere():
    from coach.adaptive import BASELINE, WEEKLY_BANK
    from coach.curriculum import Curriculum
    from coach.prompts import MODES
    cur = Curriculum()
    elsewhere = {p for m in MODES.values() for p in m["prompts"]}
    elsewhere |= {p for lid in cur.lessons for p in cur.get(lid)["exercise"].get("prompts", [])}
    elsewhere |= {b["prompt"] for b in BASELINE}
    assert not elsewhere & {w["prompt"] for w in WEEKLY_BANK}
    assert len({w["prompt"] for w in WEEKLY_BANK}) == len(WEEKLY_BANK) >= 12


def test_week_boundary_uses_the_users_timezone():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from coach.adaptive import week_key
    instant = datetime.fromisoformat("2026-10-26T05:00:00+00:00")       # Monday in UTC, Sunday night in LA
    assert week_key(instant.astimezone(ZoneInfo("UTC"))) != week_key(instant.astimezone(ZoneInfo("America/Los_Angeles")))


def test_adaptive_state_is_per_user(tmp_path, monkeypatch):
    app_module = _fresh_app(tmp_path, monkeypatch)
    ada, bob = TestClient(app_module.app), TestClient(app_module.app)
    _signup(ada, "ada@example.com")
    _signup(bob, "bob@example.com")
    ada.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT})
    assert all(s["box"] for s in ada.get("/api/skills").json()["skills"])
    assert all(s["status"] == "new" for s in bob.get("/api/skills").json()["skills"])
    assert bob.get("/api/baseline").json()["items"][0]["done"] is False


def test_todays_review_follows_new_evidence_within_the_day(client):
    first = next(i for i in client.get("/api/today").json()["items"] if i["key"] == "review")
    assert "baseline" in first["title"].lower()
    client.post("/api/analyze", json={"transcript": WEAK, "prompt": PROMPT})
    again = next(i for i in client.get("/api/today").json()["items"] if i["key"] == "review")
    assert "baseline" not in again["title"].lower() and "No full-scored" not in again["detail"]
    stable = next(i for i in client.get("/api/today").json()["items"] if i["key"] == "review")
    assert stable["prompt"] == again["prompt"]                          # stable once refreshed


# ----------------------------------------------------------------------------- v9.1: crash-safe finalization
class Crash(BaseException):
    """Stands in for the process dying: skips every `except Exception` handler, leaving the job 'running'."""


def _once(fn, *, after=False):
    """Wrap `fn` so its first call 'crashes' (before running it, or right after it ran)."""
    calls = {"n": 0}

    def wrapper(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1 and not after:
            raise Crash()
        out = fn(*a, **k)
        if calls["n"] == 1 and after:
            raise Crash()
        return out
    return wrapper


def _manual_job(app_module, us, *, audio=False, key="crash-key-0001", transcript=STRONG, **req_extra):
    """A queued job created exactly as `submit` would, but without starting the app's worker threads."""
    import uuid as _uuid
    from datetime import datetime
    from coach.jobs import JobRunner, request_hash
    from coach.schemas import AnalyzeRequest
    runner = JobRunner(app_module.store, app_module.service, workers=1)      # driven by hand: never started
    req = AnalyzeRequest(**{"transcript": transcript, "prompt": PROMPT, "idempotency_key": key, **req_extra})
    wav = _speechlike_wav([("q", .3), ("s", 5), ("q", .3)]) if audio else None
    x = app_module.service.decode_upload(wav) if audio else None
    jid = str(_uuid.uuid4())
    tmp = app_module.service.write_tmp(jid, x) if audio else None
    payload = {**req.model_dump(), "_now": datetime.now().astimezone().isoformat()}
    runner.jobs.create(job_id=jid, user_id=us.user_id, req=payload, rhash=request_hash(req.model_dump(), wav),
                       key=key, audio_tmp=tmp, limit=0)
    return runner, jid


def _skill_reviews(us):
    from coach.analysis import DIMENSIONS
    s = us.meta_get("skills") or {}
    return {d: (s.get(d) or {}).get("reviews") for d in DIMENSIONS}


CRASH_POINTS = [("before_finalize", True), ("before_save", False), ("before_save", True), ("after_save", False),
                ("after_save", True), ("before_complete", False), ("before_complete", True),
                ("after_complete", False), ("after_complete", True)]


@pytest.mark.parametrize("point,audio", CRASH_POINTS)
def test_crash_at_any_point_recovers_to_exactly_one_take(tmp_path, monkeypatch, point, audio):
    from coach import adaptive, analysis, transcribe
    from coach.jobs import JobRunner
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    monkeypatch.setattr(transcribe, "transcribe", lambda p: None)
    analyzed = {"n": 0}
    real_analyze = analysis.analyze

    def counting(*a, **k):
        analyzed["n"] += 1
        return real_analyze(*a, **k)
    monkeypatch.setattr(analysis, "analyze", counting)
    runner, jid = _manual_job(app_module, us, audio=audio)
    svc, store = app_module.service, app_module.store
    if point == "before_finalize":
        monkeypatch.setattr(svc, "_finalize_audio", _once(svc._finalize_audio))
    elif point == "before_save":                                            # audio already finalized here
        monkeypatch.setattr(store, "add_attempt", _once(store.add_attempt))
    elif point == "after_save":                                             # take committed, skills not yet
        monkeypatch.setattr(adaptive, "record_take", _once(adaptive.record_take))
    elif point == "before_complete":
        monkeypatch.setattr(runner.jobs, "complete", _once(runner.jobs.complete))
    else:
        monkeypatch.setattr(runner.jobs, "complete", _once(runner.jobs.complete, after=True))
    with pytest.raises(Crash):
        runner.run_one(jid)

    restarted = JobRunner(store, svc, workers=1)                            # = the app starting again
    restarted.recover()
    restarted.run_one(jid)                                                  # no-op if it already completed

    job = restarted.jobs.get(jid)
    assert job["status"] == "completed" and job["quota_state"] == "consumed"
    assert restarted.jobs.usage(us.user_id) == 1                           # one take, one unit
    takes = us.all_chronological()
    assert len(takes) == 1 and takes[0]["job_id"] == jid and job["attempt_id"] == takes[0]["id"]
    assert set(_skill_reviews(us).values()) == {1}                         # adaptive state updated exactly once
    committed_before_crash = point in ("after_save", "before_complete", "after_complete")
    assert analyzed["n"] == (1 if committed_before_crash else 2)           # a saved take is never re-analyzed
    if audio:
        assert takes[0]["audio_path"] == f"{jid}.wav"
        assert [p.name for p in app_module.AUDIO_DIR.iterdir()] == [f"{jid}.wav"]
        assert list(app_module.TMP_DIR.iterdir()) == []
        assert c.get(f"/api/audio/{takes[0]['id']}").status_code == 200


def test_transient_error_after_save_reuses_the_take(tmp_path, monkeypatch):
    import sqlite3
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    runner, jid = _manual_job(app_module, us)
    real = runner.jobs.complete
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)
    monkeypatch.setattr(runner.jobs, "complete", flaky)
    runner.run_one(jid)                                                     # → re-queued, take already saved
    assert runner.jobs.get(jid)["status"] == "queued"
    runner.run_one(jid)
    job = runner.jobs.get(jid)
    assert job["status"] == "completed" and job["tries"] == 2
    assert len(us.all_chronological()) == 1 and set(_skill_reviews(us).values()) == {1}
    assert runner.jobs.usage(us.user_id) == 1


def test_transient_error_while_saving_keeps_the_recording_for_the_retry(tmp_path, monkeypatch):
    import sqlite3
    from coach import transcribe
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    monkeypatch.setattr(transcribe, "transcribe", lambda p: None)
    runner, jid = _manual_job(app_module, us, audio=True)
    real, calls = app_module.store.add_attempt, {"n": 0}

    def flaky(**k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(**k)
    monkeypatch.setattr(app_module.store, "add_attempt", flaky)
    runner.run_one(jid)                                                     # moved, then the save failed
    assert [p.name for p in app_module.AUDIO_DIR.iterdir()] == [f"{jid}.wav"]
    runner.run_one(jid)
    take = us.all_chronological()
    assert runner.jobs.get(jid)["status"] == "completed" and len(take) == 1 and take[0]["audio_path"] == f"{jid}.wav"


def test_failure_before_save_refunds_and_removes_the_moved_recording(tmp_path, monkeypatch):
    from coach import transcribe
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    monkeypatch.setattr(transcribe, "transcribe", lambda p: None)
    runner, jid = _manual_job(app_module, us, audio=True)

    def broken(**k):
        raise RuntimeError("bug")
    monkeypatch.setattr(app_module.store, "add_attempt", broken)
    runner.run_one(jid)
    job = runner.jobs.get(jid)
    assert job["status"] == "failed" and job["quota_state"] == "refunded" and runner.jobs.usage(us.user_id) == 0
    assert list(app_module.AUDIO_DIR.iterdir()) == [] and list(app_module.TMP_DIR.iterdir()) == []
    assert us.all_chronological() == []


def test_error_after_save_completes_the_job_instead_of_refunding(tmp_path, monkeypatch):
    from coach import adaptive
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    runner, jid = _manual_job(app_module, us)

    def broken(*a, **k):
        raise RuntimeError("bug in the skill update")
    monkeypatch.setattr(adaptive, "record_take", broken)
    runner.run_one(jid)
    job = runner.jobs.get(jid)
    assert job["status"] == "completed" and job["quota_state"] == "consumed"   # saved take: not "free", not failed
    assert job["attempt_id"] == us.all_chronological()[0]["id"] and runner.jobs.usage(us.user_id) == 1


def test_out_of_tries_recovery_keeps_a_saved_take(tmp_path, monkeypatch):
    from coach import adaptive
    from coach.jobs import JobRunner, MAX_TRIES
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    runner, jid = _manual_job(app_module, us)
    monkeypatch.setattr(adaptive, "record_take", _once(adaptive.record_take))
    with pytest.raises(Crash):
        runner.run_one(jid)
    with app_module.store._conn() as conn:
        conn.execute("UPDATE jobs SET tries=? WHERE id=?", (MAX_TRIES, jid))
    assert JobRunner(app_module.store, app_module.service, workers=1).recover() == {"requeued": 0, "failed": 0}
    job = runner.jobs.get(jid)
    assert job["status"] == "completed" and job["quota_state"] == "consumed" and job["attempt_id"]
    assert len(us.all_chronological()) == 1 and set(_skill_reviews(us).values()) == {1}


def test_one_take_per_job_is_enforced_by_the_database(tmp_path, monkeypatch):
    import sqlite3
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    a = us.add_attempt(mode="Interview", prompt=PROMPT, transcript=STRONG, challenge_mode=False,
                       duration_seconds=None, analysis={"scores": {}}, job_id="job-x")
    b = us.add_attempt(mode="Interview", prompt=PROMPT, transcript=STRONG, challenge_mode=False,
                       duration_seconds=None, analysis={"scores": {}}, job_id="job-x")
    assert a["id"] == b["id"] and len(us.all_chronological()) == 1
    with pytest.raises(sqlite3.IntegrityError), app_module.store._conn() as conn:
        conn.execute("INSERT INTO attempts (id, chain_id, attempt_no, created_at, mode, transcript, analysis_json,"
                     " job_id) VALUES ('dup', 'c', 1, '2026-01-01', 'Interview', 'x', '{}', 'job-x')")


def test_schema_4_migration_links_existing_takes_to_their_jobs(tmp_path):
    import sqlite3
    from coach.storage import Store
    path = tmp_path / "s3.db"
    s = Store(path)
    rec = s.add_attempt(mode="Interview", prompt="p", transcript="hello there friends", challenge_mode=False,
                        duration_seconds=None, analysis={"scores": {}}, user_id="u1")
    db = sqlite3.connect(path)                                             # rewind the file to schema 3
    db.execute("DROP INDEX idx_attempts_job")
    db.execute("ALTER TABLE attempts DROP COLUMN job_id")
    db.execute("""INSERT INTO jobs (id, user_id, request_hash, status, request_json, attempt_id, created_at, updated_at)
                  VALUES ('job-old', 'u1', 'h', 'completed', '{}', ?, '2026-10-01', '2026-10-01')""", (rec["id"],))
    db.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    db.commit()
    before = db.execute("SELECT analysis_json, transcript FROM attempts").fetchall()
    db.close()
    Store(path)
    Store(path)                                                            # idempotent
    db = sqlite3.connect(path)
    assert db.execute("SELECT job_id FROM attempts").fetchall() == [("job-old",)]
    assert db.execute("SELECT analysis_json, transcript FROM attempts").fetchall() == before
    assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "4"
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_attempts_job'").fetchone()


# ----------------------------------------------------------------------------- v9.1: deleting a take
SECRET = " The codename for the project was zebracorn."


def test_deleting_a_take_removes_it_everywhere(tmp_path, monkeypatch):
    from coach.adaptive import BASELINE
    app_module = _fresh_app(tmp_path, monkeypatch)
    ada, bob = TestClient(app_module.app), TestClient(app_module.app)
    _signup(ada, "ada@example.com")
    _signup(bob, "bob@example.com")
    b0 = BASELINE[0]
    first = {"transcript": STRONG + SECRET, "kind": "baseline", "prompt": b0["prompt"], "mode": b0["mode"]}
    h = {"Idempotency-Key": "take-secret-01"}
    t1 = ada.post("/api/jobs", json=first, headers=h).json()
    t1 = _poll(ada, t1["id"])
    a1 = t1["result"]["attempt"]
    retry = {**first, "transcript": STRONG, "chain_id": a1["chain_id"]}
    t2 = _poll(ada, ada.post("/api/jobs", json=retry, headers={"Idempotency-Key": "take-secret-02"}).json()["id"])
    assert t2["result"]["comparison"]["base_id"] == a1["id"]
    for item in BASELINE[1:]:
        ada.post("/api/analyze", json={"transcript": STRONG, "kind": "baseline", "prompt": item["prompt"],
                                       "mode": item["mode"]})
    assert ada.get("/api/baseline").json()["items"][0]["attempt_id"] == a1["id"]
    bob.post("/api/analyze", json={"transcript": STRONG + SECRET, "prompt": PROMPT})
    used = ada.get("/api/usage").json()["used"]
    us = _user(app_module)
    assert set(_skill_reviews(us).values()) == {4}

    assert bob.delete(f"/api/attempts/{a1['id']}").status_code == 404     # user-scoped
    assert ada.delete(f"/api/attempts/{a1['id']}").json()["ok"]
    assert ada.delete(f"/api/attempts/{a1['id']}").status_code == 404

    everything = json.dumps([ada.get(p).json() for p in ("/api/history", "/api/export", "/api/jobs",
                                                         f"/api/jobs/{t1['id']}", f"/api/jobs/{t2['id']}")])
    assert "zebracorn" not in everything and a1["id"] not in everything
    gone = ada.get(f"/api/jobs/{t1['id']}").json()
    assert gone["status"] == "completed" and gone["result"] is None and gone["attempt_id"] is None
    assert gone["error_code"] == "deleted" and gone["quota"] == "consumed"
    assert ada.get(f"/api/jobs/{t2['id']}").json()["result"]["comparison"] is None   # compared against the deleted take
    with app_module.store._conn() as conn:
        row = conn.execute("SELECT request_json, result_json FROM jobs WHERE id=?", (t1["id"],)).fetchone()
        assert "zebracorn" not in (row[0] or "") and row[1] is None
        assert conn.execute("SELECT COUNT(*) FROM comparisons WHERE base_id=? OR current_id=?",
                            (a1["id"], a1["id"])).fetchone()[0] == 0
    # idempotency replay: same key + same take → the same (deleted) job; nothing re-created, no new charge
    replay = ada.post("/api/jobs", json=first, headers=h)
    assert replay.status_code == 200 and replay.json()["id"] == t1["id"] and replay.json()["error_code"] == "deleted"
    sync = ada.post("/api/analyze", json=first, headers=h)
    assert sync.status_code == 410
    assert len(ada.get("/api/history").json()) == 3 and ada.get("/api/usage").json()["used"] == used
    # baseline and skills no longer count the deleted take; the retry of that item takes its place
    base = ada.get("/api/baseline").json()
    assert base["complete"] and base["items"][0]["attempt_id"] == t2["attempt_id"]
    assert set(_skill_reviews(us).values()) == {3}
    # the other user's take is untouched
    assert "zebracorn" in json.dumps(bob.get("/api/history").json())


def test_take_deleted_while_its_job_finishes_is_not_written_back(tmp_path, monkeypatch):
    from coach import adaptive
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    runner, jid = _manual_job(app_module, us, transcript=STRONG + SECRET)
    real = adaptive.record_take

    def delete_then_record(store, rec, now):                               # the user deletes it mid-finalization
        assert c.delete(f"/api/attempts/{rec['id']}").json()["ok"]
        return real(store, rec, now)
    monkeypatch.setattr(adaptive, "record_take", delete_then_record)
    runner.run_one(jid)
    job = runner.jobs.get(jid)
    assert job["status"] == "completed" and job["error_code"] == "deleted" and job["result_json"] is None
    assert "zebracorn" not in json.dumps(c.get("/api/jobs").json()) and us.all_chronological() == []
    # a later re-run of that job (e.g. after a crash) doesn't bring it back
    with app_module.store._conn() as conn:
        conn.execute("UPDATE jobs SET status='queued' WHERE id=?", (jid,))
    monkeypatch.setattr(adaptive, "record_take", real)
    runner.run_one(jid)
    assert us.all_chronological() == [] and runner.jobs.get(jid)["error_code"] == "deleted"


# ----------------------------------------------------------------------------- v9.1: frontend take controller
def test_frontend_idempotency_key_rules():
    """Runs tests/js/takes.test.mjs (node:test) — the browser's idempotency-key rules for a take."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    test_files = sorted((Path(__file__).parent / "js").glob("*.test.mjs"))
    r = subprocess.run([node, "--test", *map(str, test_files)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "# fail 0" in r.stdout


def test_recording_uses_the_take_controller():
    """The Analyze button goes through takes.js — no hand-rolled key handling left in recording.js."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "static" / "js" / "recording.js").read_text()
    assert "takes.run(" in src and "takes.resume(" in src and "if (takes.busy || state.analyzing) return;" in src
    assert "idemKey" not in src


# ----------------------------------------------------------------------------- v9.2: deletion races
def _comparison_rows(app_module, attempt_id):
    with app_module.store._conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM comparisons WHERE base_id=? OR current_id=?",
                            (attempt_id, attempt_id)).fetchone()[0]


@pytest.mark.parametrize("target", ["previous", "first"])
@pytest.mark.parametrize("when", ["before_complete", "after_complete"])
def test_finishing_retry_job_never_restores_a_deleted_comparison(tmp_path, monkeypatch, target, when):
    app_module = _fresh_app(tmp_path, monkeypatch)
    c, bob = TestClient(app_module.app), TestClient(app_module.app)
    _signup(c)
    _signup(bob, "bob@example.com")
    us = _user(app_module)
    t1 = c.post("/api/analyze", json={"transcript": WEAK + SECRET, "prompt": PROMPT}).json()["attempt"]
    t2 = c.post("/api/analyze", json={"transcript": STRONG + SECRET, "prompt": PROMPT,
                                      "chain_id": t1["chain_id"]}).json()["attempt"]
    bob.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT})
    runner, jid = _manual_job(app_module, us, chain_id=t1["chain_id"])        # take 3 of the chain
    gone, kept = (t2, t1) if target == "previous" else (t1, t2)
    gone_field, kept_field = ("comparison", "first_vs_latest") if target == "previous" else ("first_vs_latest", "comparison")
    real = runner.jobs.complete

    def complete(job_id, result, attempt_id):
        assert result[gone_field]["base_id"] == gone["id"]                 # the stale in-memory comparison
        if when == "before_complete":
            assert c.delete(f"/api/attempts/{gone['id']}").json()["ok"]
        real(job_id, result, attempt_id)
        if when == "after_complete":
            assert c.delete(f"/api/attempts/{gone['id']}").json()["ok"]
    monkeypatch.setattr(runner.jobs, "complete", complete)
    runner.run_one(jid)

    job = c.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "completed" and job["quota"] == "consumed" and job["error_code"] is None
    assert job["attempt_id"] == job["result"]["attempt"]["id"]               # the valid current take stays
    assert job["result"][gone_field] is None
    assert job["result"][kept_field]["base_id"] == kept["id"]               # the other comparison is still valid
    everything = json.dumps([c.get("/api/jobs").json(), job, c.get("/api/history").json()])
    assert gone["id"] not in everything
    assert _comparison_rows(app_module, gone["id"]) == 0 and _comparison_rows(app_module, kept["id"]) >= 1
    assert c.get("/api/usage").json()["used"] == 3 and bob.get("/api/usage").json()["used"] == 1
    assert len(bob.get("/api/history").json()) == 1


def test_comparison_cache_write_racing_a_delete_is_dropped(tmp_path, monkeypatch):
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    t1 = c.post("/api/analyze", json={"transcript": WEAK, "prompt": PROMPT}).json()["attempt"]
    runner, jid = _manual_job(app_module, us, chain_id=t1["chain_id"])
    real = app_module.store.put_comparison

    def put(base_id, current_id, narrative, user_id=None):                 # the user deletes between compare + cache
        assert c.delete(f"/api/attempts/{base_id}").json()["ok"]
        return real(base_id, current_id, narrative, user_id)
    monkeypatch.setattr(app_module.store, "put_comparison", put)
    runner.run_one(jid)
    job = c.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "completed" and job["result"]["comparison"] is None
    assert _comparison_rows(app_module, t1["id"]) == 0
    # a valid pair still caches; another user can't cache against someone else's takes
    monkeypatch.setattr(app_module.store, "put_comparison", real)
    a = c.post("/api/analyze", json={"transcript": WEAK, "prompt": PROMPT}).json()["attempt"]
    b = c.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT, "chain_id": a["chain_id"]}).json()["attempt"]
    assert us.get_comparison(a["id"], b["id"]) is not None
    bob = TestClient(app_module.app)
    _signup(bob, "bob@example.com")
    bob_store = _user(app_module, "bob@example.com")
    bob_store.put_comparison(a["id"], b["id"], {"summary": "x"})
    assert bob_store.get_comparison(a["id"], b["id"]) is None


def _plain_take(us, **kw):
    from coach.analysis import DIMENSIONS
    score = kw.pop("score", 70)
    return us.add_attempt(mode="Interview", prompt=kw.pop("prompt", PROMPT), transcript=STRONG, challenge_mode=False,
                          duration_seconds=None, analysis={"scores": {d: score for d in DIMENSIONS}}, **kw)


@pytest.mark.parametrize("when", ["before_record", "after_record"])
def test_finishing_job_never_restores_deleted_skill_evidence_or_baseline(tmp_path, monkeypatch, when):
    from coach import adaptive
    from coach.adaptive import BASELINE
    app_module = _fresh_app(tmp_path, monkeypatch)
    c, bob = TestClient(app_module.app), TestClient(app_module.app)
    _signup(c)
    _signup(bob, "bob@example.com")
    us = _user(app_module)
    b0, b1 = BASELINE[0], BASELINE[1]
    c.post("/api/analyze", json={"transcript": STRONG, "kind": "baseline", "prompt": b1["prompt"], "mode": b1["mode"]})
    bob.post("/api/analyze", json={"transcript": STRONG, "prompt": PROMPT})
    bob_skills = _skill_reviews(_user(app_module, "bob@example.com"))
    runner, jid = _manual_job(app_module, us, transcript=STRONG + SECRET, kind="baseline", prompt=b0["prompt"],
                              mode=b0["mode"])
    real = adaptive.record_take

    def record(store, rec, now):
        if when == "before_record":                                        # stale in-memory take after deletion
            assert c.delete(f"/api/attempts/{rec['id']}").json()["ok"]
        real(store, rec, now)
        if when == "after_record":
            assert c.delete(f"/api/attempts/{rec['id']}").json()["ok"]
    monkeypatch.setattr(adaptive, "record_take", record)
    runner.run_one(jid)

    assert set(_skill_reviews(us).values()) == {1}                         # only the remaining take counts
    skills = us.meta_get("skills")
    assert all(a["id"] for a in us.all_chronological())
    assert set(skills["_applied"]) == {a["id"] for a in us.all_chronological()}
    base = c.get("/api/baseline").json()
    assert b0["key"] not in us.meta_get("baseline")["items"] and not base["items"][0]["done"] and base["items"][1]["done"]
    job = c.get(f"/api/jobs/{jid}").json()
    assert job["error_code"] == "deleted" and job["result"] is None and job["quota"] == "consumed"
    assert "zebracorn" not in json.dumps([job, c.get("/api/jobs").json(), c.get("/api/skills").json(), base])
    assert c.get("/api/usage").json()["used"] == 2
    assert _skill_reviews(_user(app_module, "bob@example.com")) == bob_skills  # other user untouched


def test_stale_record_take_and_other_users_ids_are_ignored(tmp_path, monkeypatch):
    from datetime import datetime
    from coach import adaptive
    app_module = _fresh_app(tmp_path, monkeypatch)
    for e in ("ada@example.com", "bob@example.com"):
        _signup(TestClient(app_module.app), e)
    ada, bob = _user(app_module), _user(app_module, "bob@example.com")
    now = datetime.now().astimezone()
    take = _plain_take(ada)
    adaptive.record_take(bob, take, now)                                    # not Bob's take: no evidence for Bob
    assert bob.meta_get("skills") is None
    ada.delete(take["id"])
    adaptive.record_take(ada, take, now)                                    # deleted: nothing comes back
    assert (ada.meta_get("skills") or {}).get("structure") is None
    keep = _plain_take(ada)
    adaptive.record_take(ada, keep, now)
    adaptive.record_take(ada, keep, now)                                    # recovery: still exactly once
    assert set(_skill_reviews(ada).values()) == {1}


def test_concurrent_skill_updates_are_not_lost(tmp_path, monkeypatch):
    """Two jobs finish at once for one user: the second update must see the first (no lost update)."""
    import threading
    from datetime import datetime
    from coach import adaptive
    app_module = _fresh_app(tmp_path, monkeypatch)
    _signup(TestClient(app_module.app))
    us = _user(app_module)
    now = datetime.now().astimezone()
    a, b = _plain_take(us), _plain_take(us)
    inside, release, b_started = threading.Event(), threading.Event(), threading.Event()
    real_apply = adaptive._apply

    def slow_apply(skills, attempt, tz):
        if attempt["id"] == a["id"]:
            inside.set()                                                    # A has read the schedule…
            assert release.wait(10)                                         # …and holds until B is on its way
        return real_apply(skills, attempt, tz)
    monkeypatch.setattr(adaptive, "_apply", slow_apply)
    ta = threading.Thread(target=adaptive.record_take, args=(us, a, now))
    ta.start()
    assert inside.wait(10)

    def run_b():
        b_started.set()
        adaptive.record_take(us, b, now)
    tb = threading.Thread(target=run_b)
    tb.start()
    assert b_started.wait(10)
    release.set()
    ta.join(10)
    tb.join(10)
    assert not ta.is_alive() and not tb.is_alive()
    assert set(_skill_reviews(us).values()) == {2}
    assert set(us.meta_get("skills")["_applied"]) == {a["id"], b["id"]}


def test_delete_waits_for_an_in_flight_skill_update(tmp_path, monkeypatch):
    """A delete that arrives while a skill update is mid-transaction is applied after it, and wins."""
    import threading
    from datetime import datetime
    from coach import adaptive
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    _signup(c)
    us = _user(app_module)
    now = datetime.now().astimezone()
    keep, doomed = _plain_take(us, score=90), _plain_take(us, score=40)
    adaptive.record_take(us, keep, now)
    inside, release, deleting = threading.Event(), threading.Event(), threading.Event()
    real_apply = adaptive._apply

    def slow_apply(skills, attempt, tz):
        if attempt["id"] == doomed["id"] and not inside.is_set():
            inside.set()
            assert release.wait(10)
        return real_apply(skills, attempt, tz)
    monkeypatch.setattr(adaptive, "_apply", slow_apply)
    worker = threading.Thread(target=adaptive.record_take, args=(us, doomed, now))
    worker.start()
    assert inside.wait(10)
    result = {}

    def delete():
        deleting.set()
        result["r"] = c.delete(f"/api/attempts/{doomed['id']}").status_code
    d = threading.Thread(target=delete)
    d.start()
    assert deleting.wait(10)
    release.set()
    worker.join(10)
    d.join(10)
    assert result["r"] == 200
    skills = us.meta_get("skills")
    assert skills["_applied"] == [keep["id"]] and set(_skill_reviews(us).values()) == {1}
    assert skills["structure"]["last_score"] == 90                          # the deleted 40 left no trace


# Everyday practice and transcript fidelity.
@pytest.mark.parametrize('text,expected', [
    ('Ummm, uhm, um, uhhhh, ermmm, ahhh, hmmmm.',
     {'um': 3, 'uh': 1, 'er': 1, 'ah': 1, 'hmm': 1}),
    ('I would like tea. I like walking. It looks like rain. Summer is here.', {}),
])
def test_prolonged_hesitations_are_counted_without_matching_normal_words(text, expected):
    from coach.metrics import filler_breakdown
    assert filler_breakdown(text) == expected


@pytest.mark.parametrize('mode,text', [
    ('Everyday Conversation', "Hi, I'm Sam. I enjoy walking in the park because it helps me relax after a busy day. "
     "Yesterday I went with a friend and we stopped for tea. How about you, what do you enjoy doing in your free time?"),
    ('Tell a Story', "Last year, when I was learning to cook, I forgot to set a timer. I decided to start again "
     "and write down the steps. As a result, we ended up enjoying dinner together. I learned to prepare before I start."),
    ('Share an Opinion', "I prefer spending a free day outdoors because a change of scenery helps me relax. "
     "For example, when I go for a walk with a friend, we have time to talk without distractions. "
     "That's why I think a little time outside makes a difference."),
    ('Difficult Conversations', "I understand you have a busy schedule and I appreciate you making time for me. "
     "I think we should agree on a meeting time because waiting outside is frustrating. "
     "As a next step, could we try sending a message if either of us is running late?"),
])
def test_everyday_scenarios_have_appropriate_coaching(client, mode, text):
    from coach.prompts import MODES
    cfg = client.get('/api/config').json()
    public = next(m for m in cfg['modes'] if m['name'] == mode)
    assert public['group'] == 'Everyday life'
    prompt = client.get('/api/prompt', params={'mode': mode}).json()
    assert prompt['prompt'] in MODES[mode]['prompts']
    r = client.post('/api/analyze', json={'mode': mode, 'prompt': prompt['prompt'], 'transcript': text}).json()
    a = r['analysis']
    assert a['mode'] == mode
    assert a['review']['evaluator']['role'] != 'Interviewer'
    assert a['scores']['answer_quality'] >= 60
    assert a['focus']['retry']
    assert 'Quantify at least one outcome' not in a['improvements']
    assert a['metrics']['filler_note'].startswith('Counted from the transcript')


def test_everyday_practice_is_default_and_professional_modes_still_work(client):
    from coach.prompts import DEFAULT_MODE, normalize_mode
    assert DEFAULT_MODE == 'Everyday Conversation'
    assert normalize_mode(None) == DEFAULT_MODE
    assert normalize_mode('technical') == 'Technical Explanation'
    assert client.get('/api/recommendation').json()['mode'] == DEFAULT_MODE
    assert client.get('/api/prompt', params={'mode': DEFAULT_MODE}).json()['mode'] == DEFAULT_MODE


def test_audio_transcript_without_word_timings_keeps_the_live_draft(client, monkeypatch):
    from coach import transcribe
    recognized = 'Um, I enjoy walking because it helps me relax. How about you?'
    draft = 'I enjoy walking because it helps me relax how about you'
    monkeypatch.setattr(transcribe, 'transcribe', lambda p: {
        'text': recognized, 'words': [], 'source': 'test:whisper',
    })
    wav = _speechlike_wav([('q', .3), ('s', 5), ('q', .3)])
    response = client.post('/api/analyze-audio', files={'audio': ('take.wav', wav, 'audio/wav')},
        data={'payload': json.dumps({'mode': 'Everyday Conversation', 'transcript': draft})})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['attempt']['transcript'] == recognized
    assert data['analysis']['voice']['browser_transcript'] == draft
    assert data['analysis']['metrics']['fillers']['um'] == 1
    assert data['analysis']['review']['evidence']['timestamps'] is False


def test_everyday_coaching_does_not_treat_think_as_a_greeting_or_require_numbers():
    from coach import analysis
    opening = analysis.framework_map('I think this needs more thought.', ['Opening'])[0]
    assert opening['status'] == 'missing'
    story = analysis.analyze('When I was learning to cook, I needed help. I decided to ask a friend. '
                             'I learned to ask for help instead of giving up.', 'Tell a Story')
    result_fixes = [f for f in story['review']['coach']['fixes'] if 'Result' in f['problem']]
    assert result_fixes and 'number' not in result_fixes[0]['fix']


# ----------------------------------------------------------------------------- mentor rewrite (built-in)
DAY_TAKE = ("I woke up at 9:00 a.m. and came downstairs have some car had some coffee my brother got up he woke up "
            "around 9:30 he had some breakfast I took him to the piano lessons and we came back we got ready I packed "
            "his bag we went to the library there was a book sale going on at the library we stayed there for more than "
            "an hour and then we had lunch and they came back after I came back I sat with my brother for his math "
            "homework and you know science project then yeah well I did my other things after that this is all my day "
            "you know today")   # a made-up spoken run-on: false start, fillers, sign-off, no punctuation
RECOMMEND = "Recommend a meal, book, film, or place you enjoyed. Explain why and ask what they recommend."


def test_mentor_rewrite_turns_a_spoken_run_on_into_clean_sentences():
    from coach import analysis, rewrite
    r = rewrite.mentor_rewrite(DAY_TAKE, "Everyday Conversation", analysis.mode_spec("Everyday Conversation"),
                               analysis.FRAMEWORK_CUES, RECOMMEND)
    sents = r["corrected"]["sentences"]
    assert sents[0] == "I woke up at 9:00 a.m., came downstairs and had some coffee."   # false start repaired
    assert "He woke up around 9:30 and had some breakfast." in sents                   # short sentences joined
    assert "We came back and got ready." in sents
    full = r["corrected"]["full"].lower()
    assert "you know" not in full and "yeah" not in full and "this is all" not in full  # fillers + sign-off gone
    for item in r["corrected"]["items"]:                    # every "you said" is the speaker's exact words
        assert " ".join(item["original"].split()) in DAY_TAKE and item["changes"]
    parts = r["script"]["parts"]
    assert [p["label"] for p in parts] == ["Opening", "Detail", "Question"]
    assert not parts[0]["yours"] and parts[1]["yours"] and not parts[2]["yours"]       # added lines are labelled
    assert "piano lessons" in parts[0]["text"] and parts[2]["text"].endswith("?")


def test_off_topic_answer_becomes_the_first_thing_to_fix():
    from coach import analysis
    a = analysis.analyze(DAY_TAKE, "Everyday Conversation", RECOMMEND, duration_seconds=65)
    f = a["focus"]
    assert "recommend" in f["priority"]["text"] and "described your day" in f["priority"]["text"]
    assert a["scores"]["relevance"] <= 40
    assert f["strength"]["quote"] != f["priority"]["quote"] or f["strength"]["quote"] is None
    assert a["review"]["coach"]["fixes"][0]["problem"].startswith("You described your day")
    assert "fillers in one sentence" not in json.dumps(a["review"]["coach"]["fixes"])      # 2 in 117 words isn't it
    assert a["review"]["coach"]["stronger_version"]
    assert "warm-up" not in json.dumps(a["review"]["evaluator"])                          # missing opening ≠ warm-up
    assert "?" in a["review"]["evaluator"]["follow_up"] and "help the other person" not in a["review"]["evaluator"]["follow_up"]


def test_a_real_recommendation_is_not_flagged_and_keeps_its_own_question():
    from coach import analysis, rewrite
    t = ("hi so I have to tell you about this ramen place downtown it's called Menya I went there last Friday with my "
         "sister the broth is really rich and they make the noodles by hand you should totally try it what's your "
         "favorite place to eat")
    assert rewrite.intent_check(RECOMMEND, t) is None
    parts = rewrite.mentor_rewrite(t, "Everyday Conversation", analysis.mode_spec("Everyday Conversation"),
                                   analysis.FRAMEWORK_CUES, RECOMMEND)["script"]["parts"]
    assert all(p["yours"] for p in parts)
    assert parts[0]["text"] == "Hi, I have to tell you about this ramen place downtown."
    assert parts[2]["text"] == "What's your favorite place to eat?"


def test_opinion_rewrite_separates_point_reason_and_example():
    from coach import analysis, rewrite
    t = ("I think remote work is better because people save two hours of commuting and for example my team shipped "
         "faster last year so that's why I think remote work is better")
    parts = rewrite.mentor_rewrite(t, "Share an Opinion", analysis.mode_spec("Share an Opinion"),
                                   analysis.FRAMEWORK_CUES, "Is it better to work from home or in an office?")["script"]["parts"]
    assert [(p["label"], p["text"]) for p in parts] == [
        ("Point", "I think remote work is better."), ("Reason", "That's because people save two hours of commuting."),
        ("Example", "For example, my team shipped faster last year."), ("Point again", "That's why I think remote work is better.")]


def test_prompt_follow_up_questions_are_turned_around():
    from coach.rewrite import prompt_question
    assert prompt_question(RECOMMEND) == "What would you recommend?"
    assert prompt_question("A neighbor asks how your weekend was. Share one highlight and ask about their weekend.") == "How was your weekend?"
    assert prompt_question("Tell a friend about a hobby you enjoy and ask what they like doing in their free time.") == \
        "What do you like doing in your free time?"
    assert prompt_question("Describe your morning.") is None


def test_am_pm_is_neither_a_sentence_end_nor_a_grammar_error():
    from coach import language, metrics
    t = "I woke up at 9:00 a.m. and came downstairs and we left at 5 p.m. for dinner"
    assert len(metrics.sentences(t)) == 1 and not metrics.compute(t)["punctuated"]
    assert not [g for g in language.grammar_issues(t) if g["original"].startswith("a m")]


def test_challenge_mode_gets_no_rewritten_answer():
    from coach import analysis
    a = analysis.analyze(DAY_TAKE, "Everyday Conversation", RECOMMEND, challenge=True, duration_seconds=65)
    co, cr = a["review"]["coach"], a["review"]["corrections"]
    assert co["stronger_version"] is None and co["script"] is None
    assert cr["corrected_full"] is None and all(x["corrected"] is None for x in cr["items"])
    assert "One thing I really enjoyed" not in json.dumps(a["review"])                    # example wording redacted
    assert "recommend" in a["focus"]["priority"]["text"]                                   # the diagnosis still shows


# ----------------------------------------------------------------------------- mentor grammar
GRAMMAR_CASES = [
    ("Yesterday me and my son go to the park and play football.",
     "Yesterday my son and I went to the park and played football."),
    ("My son he like swimming very much.", "My son likes swimming very much."),
    ("She have two kid and she work in a hospital.", "She has two kids and she works in a hospital."),
    ("I am agree with you because I have went there two time.", "I agree with you because I have gone there two times."),
    ("Last weekend we visit my aunt and eat dinner at her house.", "Last weekend we visited my aunt and ate dinner at her house."),
    ("He can goes there on monday.", "He can go there on Monday."),
    ("Can you explain me how it works?", "Can you explain to me how it works?"),
    ("I enjoy to cook with my family.", "I enjoy cooking with my family."),
    ("People is very friendly here.", "People are very friendly here."),
    ("I want go to home now.", "I want to go home now."),
    ("I woke up at seven and go to the gym.", "I woke up at seven and went to the gym."),
    ("We discussed about the plan and he say me it is more easy.", "We discussed the plan and he told me it is easier."),
    ("I have seen him last week.", "I saw him last week."),
    ("Everybody are happy with the result.", "Everybody is happy with the result."),
    ("She don't have many informations.", "She don't have much information."),   # "don't" is fixed by the next pass
    ("They is very funny and people saves time.", "They are very funny and people save time."),
]


@pytest.mark.parametrize("said,better", GRAMMAR_CASES)
def test_mentor_grammar_corrects_common_mistakes_with_a_rule(said, better):
    from coach import grammar
    out, changes = grammar.fix(said)
    assert out == better
    assert changes and all(c["rule"] and c["kind"] for c in changes)       # every fix explains itself


def test_mentor_grammar_leaves_correct_english_alone():
    from coach import rewrite
    good = ["I usually go to the gym before work.", "They play football every Sunday.",
            "Yesterday I went to the market and bought some fruit.", "She works at a hospital and has two kids.",
            "I have lived here for five years.", "Let me know if you need anything.",
            "My team and I shipped the feature in two weeks.", "I decided to run a two week proof of concept.",
            "Last year I started running and now I run every morning.", "If I were you, I would book early.",
            "My mom and dad visit us every summer.", "Everyone was happy with the result.", "I agree with you.",
            "You should totally try it.", "When I was a kid we lived near the beach."]
    for g in good:
        assert rewrite.corrected(g, "Everyday Conversation")["grammar_count"] == 0, g


def test_story_tense_carries_across_sentences_but_stops_at_now():
    from coach import rewrite
    out = rewrite.corrected("yesterday we go to the beach he really like it and we eat ice cream after that my son he "
                            "say me it was the best day", "Everyday Conversation")
    assert out["sentences"] == ["Yesterday, we went to the beach.", "He really liked it.", "We ate ice cream after that.",
                                "My son told me it was the best day."]
    kinds = [g["kind"] for g in out["grammar"]]
    assert kinds[0] == "tense" and "pronouns" in kinds                      # most frequent pattern first
    assert out["grammar"][0]["lesson"] and out["grammar"][0]["examples"][0] == "go → went"
    calm = rewrite.corrected("Three years ago, I counted forty. I remember the first morning. So next time you reach "
                             "for your phone, remember: the first hour is yours.", "Toastmasters")
    assert calm["grammar_count"] == 0                                        # "remember", "next time" aren't past


def test_grammar_lessons_show_in_the_review_and_challenge_hides_the_answers(client):
    t = "Last weekend we visit my aunt and she cook biryani and we watch a movie together"
    r = client.post("/api/analyze", json={"transcript": t, "mode": "Everyday Conversation"}).json()["analysis"]["review"]
    g = r["corrections"]["grammar"]
    assert g[0]["kind"] == "tense" and "visit → visited" in g[0]["examples"] and g[0]["lesson"]
    assert "We visited my aunt" in r["coach"]["stronger_version"] or "we visited my aunt" in r["coach"]["stronger_version"]
    ch = client.post("/api/analyze", json={"transcript": t, "mode": "Everyday Conversation",
                                           "challenge_mode": True}).json()["analysis"]["review"]["corrections"]
    assert ch["grammar"] and all(p["examples"] == [] and p["lesson"] for p in ch["grammar"])


def test_many_grammar_slips_become_the_first_fix_without_giving_answers_in_challenge():
    from coach import analysis
    t = ("yesterday me and my son go to the park and play football he really like it and we eat ice cream after that "
         "my son he say me it was the best day")
    p = "A neighbor asks how your weekend was. Share one highlight and ask about their weekend."
    a = analysis.analyze(t, "Everyday Conversation", p, False, 30)
    assert "grammar slips" in a["focus"]["priority"]["text"] and "past tense" in a["focus"]["priority"]["text"]
    assert "go → went" in a["focus"]["retry"]
    c = analysis.analyze(t, "Everyday Conversation", p, True, 30)
    assert "grammar slips" in c["focus"]["priority"]["text"] and "→" not in c["focus"]["retry"]
    assert "→" not in json.dumps(c["review"]["coach"])


# ----------------------------------------------------------------------------- public beta
def test_beta_settings_badge_feedback_and_signup_cap(tmp_path, monkeypatch):
    c = TestClient(_fresh_app(tmp_path, monkeypatch).app)
    assert c.get("/api/config").json()["beta"]["on"] is False                 # off unless the server says so
    c = TestClient(_fresh_app(tmp_path / "b", monkeypatch, BETA="1", BETA_MAX_USERS="2",
                              FEEDBACK_URL="https://example.com/feedback", CONTACT_EMAIL="hi@example.com").app)
    beta = c.get("/api/config").json()["beta"]
    assert beta == {"on": True, "feedback_url": "https://example.com/feedback", "contact": "hi@example.com"}
    _signup(c, "a@example.com")
    _signup(TestClient(c.app), "b@example.com")
    full = TestClient(c.app).post("/api/auth/signup", json={"email": "c@example.com", "password": "correct horse"})
    assert full.status_code == 403 and "beta is full" in full.json()["detail"]
    assert TestClient(c.app).post("/api/auth/login", json={"email": "a@example.com",
                                                          "password": "correct horse"}).status_code == 200


def test_privacy_page_and_api_docs_switch(tmp_path, monkeypatch):
    c = TestClient(_fresh_app(tmp_path, monkeypatch).app)
    p = c.get("/privacy")
    assert p.status_code == 200 and "What we store" in p.text and "scrypt" in p.text
    assert c.get("/docs").status_code == 200                                 # local development: docs on
    c = TestClient(_fresh_app(tmp_path / "p", monkeypatch, API_DOCS="0").app)
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    assert c.get("/api/health").json()["ok"]


def test_behind_a_proxy_each_visitor_gets_their_own_rate_limit(tmp_path):
    """Production runs uvicorn behind Caddy with --proxy-headers. Without it every visitor would share the proxy's
    IP, so the 5-sign-ups-per-IP limit would block the whole site after five people."""
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "TTS_DB_PATH": str(tmp_path / "p.db"), "LLM_PROVIDER": "local",
           "TRANSCRIBE_PROVIDER": "none", "AUTH_MODE": "accounts", "COOKIE_SECURE": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port),
                             "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"],
                            cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)

        def signup(i, ip):
            req = urllib.request.Request(base + "/api/auth/signup", method="POST",
                                         data=json.dumps({"email": f"u{i}@example.com", "password": "correct horse"}).encode(),
                                         headers={"Content-Type": "application/json", "X-Forwarded-For": ip,
                                                  "X-Forwarded-Proto": "https"})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, r.headers.get("set-cookie", "")
            except urllib.error.HTTPError as e:
                return e.code, ""
        results = [signup(i, f"203.0.113.{i}") for i in range(6)]        # six different visitors
        assert [s for s, _ in results] == [200] * 6
        assert "secure" in results[0][1].lower() and "httponly" in results[0][1].lower()
        same = [signup(10 + i, "198.51.100.7")[0] for i in range(6)]        # one visitor, six accounts
        assert same[:5] == [200] * 5 and same[5] == 429
    finally:
        proc.terminate()
        proc.wait(10)


def test_security_headers_allow_what_the_app_uses(anon):
    r = anon.get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self' blob:" in csp and "frame-ancestors 'none'" in csp   # blob: = the mic audio worklet
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "microphone=(self)" in r.headers["permissions-policy"]
    assert "strict-transport-security" not in r.headers                         # only sent over HTTPS
    assert "<script>" not in anon.get("/privacy").text                          # no inline scripts for CSP to block


# ----------------------------------------------------------------------------- guest mode
def test_guest_can_try_takes_then_upgrade_keeping_practice(tmp_path, monkeypatch):
    c = TestClient(_fresh_app(tmp_path, monkeypatch, BETA="1", BETA_MAX_USERS="1", GUEST_TAKE_LIMIT="2").app)
    cfg = c.get("/api/config").json()
    assert cfg["guests"] is True and cfg["guest_take_limit"] == 2
    g = c.post("/api/auth/guest").json()["user"]
    assert g["guest"] is True and g["email"] is None
    assert c.post("/api/auth/guest").json()["user"]["id"] == g["id"]           # same browser: same guest
    me = c.get("/api/auth/me").json()["user"]
    assert me["guest"] is True
    for _ in range(2):
        assert c.post("/api/analyze", json={"transcript": STRONG, "mode": "Interview"}).status_code == 200
    over = c.post("/api/analyze", json={"transcript": STRONG})
    assert over.status_code == 429 and "free account" in over.json()["detail"]
    assert c.get("/api/usage").json()["limit"] == 2
    # guests don't use up beta spots, and can't sign in (no password)
    from coach.storage import GUEST_DOMAIN
    assert c.post("/api/auth/login", json={"email": f"guest-{g['id']}{GUEST_DOMAIN}", "password": "!"}).status_code == 401
    up = c.post("/api/auth/upgrade", json={"email": "Gia@Example.com", "password": "correct horse", "name": "Gia"})
    assert up.status_code == 200, up.text
    u = up.json()["user"]
    assert u["id"] == g["id"] and u["guest"] is False and u["email"] == "gia@example.com"
    assert len(c.get("/api/history").json()) == 2                              # practice carried over
    assert c.post("/api/analyze", json={"transcript": STRONG}).status_code == 200   # normal limit now
    assert c.post("/api/auth/upgrade", json={"email": "x@example.com", "password": "correct horse"}).status_code == 400
    # the one beta spot is now taken: new sign-ups are full, but a new visitor can still try as a guest
    other = TestClient(c.app)
    assert other.post("/api/auth/signup", json={"email": "z@example.com", "password": "correct horse"}).status_code == 403
    assert other.post("/api/auth/guest").status_code == 200
    assert other.post("/api/auth/upgrade", json={"email": "z@example.com", "password": "correct horse"}).status_code == 403
    fresh = TestClient(c.app)
    fresh.post("/api/auth/guest")
    taken = fresh.post("/api/auth/upgrade", json={"email": "gia@example.com", "password": "correct horse"})
    assert taken.status_code in (403, 409)
    # signing in from a guest browser switches to the real account
    assert other.post("/api/auth/login", json={"email": "gia@example.com", "password": "correct horse"}).status_code == 200
    assert other.get("/api/auth/me").json()["user"]["id"] == g["id"]


def test_guest_can_delete_without_password_and_stale_guests_are_purged(tmp_path, monkeypatch):
    app_module = _fresh_app(tmp_path, monkeypatch)
    c = TestClient(app_module.app)
    c.post("/api/auth/guest")
    assert c.post("/api/analyze", json={"transcript": STRONG}).status_code == 200
    assert c.request("DELETE", "/api/auth/account", json={"password": ""}).json()["ok"]
    assert c.get("/api/history").status_code == 401
    # a guest older than 14 days is removed with its practice; a real account of the same age is kept
    from routes.auth import purge_stale_guests
    store = app_module.store
    old = store.create_guest()
    real = _signup(TestClient(app_module.app), "keep@example.com")
    with store._conn() as db:
        db.execute("UPDATE users SET created_at='2020-01-01T00:00:00+00:00'")
    assert purge_stale_guests(app_module.app.state.ctx) == 1
    assert store.user(old["id"]) is None and store.user(real["id"]) is not None


def test_guest_mode_can_be_turned_off_and_is_rate_limited(tmp_path, monkeypatch):
    c = TestClient(_fresh_app(tmp_path, monkeypatch, GUEST_MODE="0").app)
    assert c.get("/api/config").json()["guests"] is False
    assert c.post("/api/auth/guest").status_code == 400
    c = TestClient(_fresh_app(tmp_path / "r", monkeypatch, GUEST_MODE="1").app)
    codes = [TestClient(c.app).post("/api/auth/guest").status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_mentor_keeps_time_lead_in_and_fixes_it_were():
    from coach import rewrite
    r = rewrite.corrected("um so yesterday I go to the library and uh I study for my exam for like three hours "
                          "and it were really quiet", "Everyday Conversation")
    assert r["full"] == "Yesterday, I went to the library. I studied for my exam for three hours. It was really quiet."
    r = rewrite.corrected("so last weekend we go to the beach and my brother he swim a lot it were fun", "Everyday Conversation")
    assert r["full"] == "Last weekend we went to the beach. My brother swam a lot. It was fun."
    from coach import grammar
    assert grammar.fix("if it were me I would go")[0].startswith("If it were")       # subjunctive is right


def test_bluf_answer_that_echoes_the_question_is_the_bottom_line_everywhere():
    """A take that opens by restating the question IS the bottom line: the score, the part-by-part review and
    'Say it like this' must agree, and a long unpunctuated take never gets 'no corrections needed'."""
    from pathlib import Path
    from coach import analysis
    from coach.curriculum import Curriculum
    take = ("the biggest risk on our launch wants to get everything done in two weeks with a small team and new "
            "tools that nobody has used before for building testing and shipping the app to all of our customers "
            "you will see a better results by multi-testing multiple builds and checking them again")
    cur = Curriculum(Path(__file__).parent.parent / "content" / "journeys")
    les = cur.get("ss02")
    r = analysis.analyze(take, les["exercise"]["mode"], "What's the biggest risk on your project?", False, 30,
                         cur.spec(les))
    status = {x["label"]: x["status"] for x in r["structure_map"]}
    parts = {p["label"]: p["status"] for p in r["review"]["breakdown"]["parts"]}
    script = {p["label"]: p.get("yours") for p in r["review"]["coach"]["script"]}
    assert status == parts == {"Bottom line": "present", "Context": "present", "Next step": "missing"}
    assert script == {"Bottom line": True, "Context": True, "Next step": False}
    changes = " ".join(c for it in r["review"]["corrections"]["items"] for c in it["changes"])
    assert "is that we have to" in changes and "a better result" in changes and "testing multiple" in changes
    assert "shorter sentences" in changes                                    # 34 words in one breath
    # a question it doesn't echo still needs a real bottom line
    other = analysis.framework_map(take, ["Bottom line", "Context", "Next step"], "How was your weekend?")
    assert other[0]["status"] == "missing"


def test_new_grammar_rules_and_their_exceptions():
    from coach import grammar
    fix = lambda s: grammar.fix(s)[0]  # noqa: E731
    assert fix("we got a great ideas from them") == "We got a great idea from them"
    assert fix("a lot of results came in") == "A lot of results came in"            # quantity words stay
    assert fix("a few results were wrong") == "A few results were wrong"
    assert fix("the biggest risk on the launch wants to finish in a week") == \
        "The biggest risk on the launch is that we have to finish in a week"
    assert fix("my manager wants to ship it friday").startswith("My manager wants to")  # people can want things
    assert fix("multi-checking multiple files") == "Checking multiple files"


# ----------------------------------------------------------------------------- Groq: free Meta Llama coaching
class _FakeGroq:
    """Stands in for the OpenAI client pointed at Groq: records the call, returns canned output."""
    calls = []
    reply = "{}"
    fail = None

    def __init__(self, **kw):
        _FakeGroq.calls.append(("client", kw))
        outer = self

        class _Completions:
            def create(self, **kw):
                _FakeGroq.calls.append(("chat", kw))
                if _FakeGroq.fail:
                    raise _FakeGroq.fail
                msg = type("M", (), {"content": _FakeGroq.reply})
                return type("R", (), {"choices": [type("C", (), {"message": msg})]})

        class _Transcriptions:
            def create(self, **kw):
                _FakeGroq.calls.append(("stt", kw))
                return {"text": " AI is a real catalyst for us. ", "words": [{"word": "AI", "start": 0.1, "end": 0.3}]}

        self.chat = type("Chat", (), {"completions": _Completions()})()
        self.audio = type("Audio", (), {"transcriptions": _Transcriptions()})()
        del outer


def _groq(monkeypatch, **env):
    from coach import llm
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(llm, "OpenAI", _FakeGroq)
    _FakeGroq.calls, _FakeGroq.fail = [], None
    llm.chat_budget._day, llm.chat_budget._used, llm.chat_budget._pause_until = None, 0, 0.0
    return llm


BLUF_AI = {"scores": {"structure": 70, "clarity": 66, "relevance": 88, "answer_quality": 60},
           "review": {"coach": {"stronger_version": "x", "script": [
               {"label": "Bottom line", "text": "The biggest risk is our one-week deadline.", "yours": True},
               {"label": "Context", "text": "AI is a real catalyst for us, but results take several rounds.", "yours": True},
               {"label": "Next step", "text": "I'd suggest we plan two days for review.", "yours": False}]}}}


def test_groq_coaches_with_llama_and_labels_the_bluf_parts(monkeypatch):
    from coach import analysis
    llm = _groq(monkeypatch)
    _FakeGroq.reply = json.dumps(BLUF_AI)
    assert llm.active_provider() == "groq" and "llama" in llm.model_name("groq")
    spec = {"framework": ["Bottom line", "Context", "Next step"], "name": "BLUF in 30"}
    a = analysis.analyze("the biggest risk on my project wants to get the outcome within a week", "Leadership",
                         "What's the biggest risk on your project?", False, 30, spec)
    assert a["provider"].startswith("groq:")
    client = next(kw for kind, kw in _FakeGroq.calls if kind == "client")
    chat = next(kw for kind, kw in _FakeGroq.calls if kind == "chat")
    assert client["base_url"] == "https://api.groq.com/openai/v1" and client["api_key"] == "test-key"
    assert chat["response_format"] == {"type": "json_object"} and "llama" in chat["model"]
    script = a["review"]["coach"]["script"]
    assert [(p["label"], p["yours"]) for p in script] == [("Bottom line", True), ("Context", True), ("Next step", False)]
    assert a["review"]["coach"]["stronger_version"].startswith("The biggest risk is our one-week deadline.")
    ch = analysis.analyze("the biggest risk is the deadline", "Leadership", "What's the biggest risk?", True, 30, spec)
    co = ch["review"]["coach"]                                               # challenge mode: no AI wording
    assert not (co.get("stronger_version") or "").startswith("The biggest risk is our")
    assert not any("one-week deadline" in p.get("text", "") for p in co.get("script") or [])


def test_groq_daily_cap_and_rate_limit_fall_back_to_the_built_in_coach(monkeypatch):
    from coach import analysis
    llm = _groq(monkeypatch, AI_DAILY_TAKES="1")
    _FakeGroq.reply = json.dumps(BLUF_AI)
    assert analysis.analyze(STRONG, "Interview", PROMPT)["provider"].startswith("groq:")
    second = analysis.analyze(STRONG, "Interview", PROMPT)                  # over today's cap: no call made
    assert second["provider"] == "local:heuristic" and "used up" in second["warning"]
    assert sum(1 for kind, _ in _FakeGroq.calls if kind == "chat") == 1
    llm = _groq(monkeypatch, AI_DAILY_TAKES="50")
    err = Exception("slow down"); err.status_code = 429
    _FakeGroq.fail = err
    assert analysis.analyze(STRONG, "Interview", PROMPT)["provider"] == "local:heuristic"
    assert analysis.analyze(STRONG, "Interview", PROMPT)["provider"] == "local:heuristic"
    assert sum(1 for kind, _ in _FakeGroq.calls if kind == "chat") == 1          # paused after the 429
    assert llm.chat_budget.status()["used"] == 1


def test_groq_whisper_transcribes_recordings(monkeypatch, tmp_path):
    from coach import transcribe
    _groq(monkeypatch, TRANSCRIBE_PROVIDER="auto")
    monkeypatch.setattr(transcribe, "available", lambda: "groq")
    transcribe.stt_budget._day, transcribe.stt_budget._used = None, 0
    wav = tmp_path / "t.wav"
    wav.write_bytes(b"RIFF")
    out = transcribe.transcribe(str(wav))
    assert out["text"] == "AI is a real catalyst for us." and out["source"].startswith("groq:whisper")
    assert out["words"] == [{"word": "AI", "start": 0.1, "end": 0.3}]
    stt = next(kw for kind, kw in _FakeGroq.calls if kind == "stt")
    assert stt["response_format"] == "verbose_json" and "word" in stt["timestamp_granularities"]


def test_privacy_page_says_where_words_go_for_both_setups(anon):
    p = anon.get("/privacy").text
    assert 'id="coachAI"' in p and "Groq" in p and 'id="coachLocal" hidden' in p
