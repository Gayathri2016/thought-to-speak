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
