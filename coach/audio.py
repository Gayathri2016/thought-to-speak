"""Acoustic analysis of a spoken take — works on the actual waveform, no API needed.

Measures what a transcript can't: pauses (where and how long), speaking vs. silent time,
pace over time, pitch variety (monotone or expressive), volume consistency and trailing off.
"""
import io
import shutil
import subprocess
import wave

import numpy as np

SR = 16000
FRAME = 0.02            # 20 ms analysis frames
MIN_PAUSE = 0.30        # silence shorter than this is just articulation
LONG_PAUSE = 1.0
STALL = 2.5


# --------------------------------------------------------------------------- loading
def load_audio(data: bytes) -> np.ndarray:
    """Return mono float32 samples at 16 kHz. Accepts WAV directly; anything else via ffmpeg."""
    if data[:4] == b"RIFF":
        try:
            return _read_wav(data)
        except (wave.Error, ValueError):
            pass
    if not shutil.which("ffmpeg"):
        raise ValueError("Unsupported audio format (send 16-bit PCM WAV, or install ffmpeg).")
    proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SR),
                           "-f", "wav", "pipe:1"], input=data, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise ValueError(f"Could not decode audio: {proc.stderr.decode(errors='ignore')[:200]}")
    return _read_wav(proc.stdout)


def _read_wav(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data)) as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        raise ValueError(f"Unsupported sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR and len(x):
        t_new = np.arange(0, len(x) / sr, 1 / SR)
        x = np.interp(t_new, np.arange(len(x)) / sr, x).astype(np.float32)
    return x


def to_wav_bytes(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


# --------------------------------------------------------------------------- core analysis
def _frames(x: np.ndarray, size: int) -> np.ndarray:
    n = len(x) // size
    return x[: n * size].reshape(n, size)


def _runs(mask: np.ndarray):
    """Yield (value, start_idx, end_idx_exclusive) runs of a boolean array."""
    if not len(mask):
        return
    edges = np.flatnonzero(np.diff(mask.astype(np.int8))) + 1
    starts = np.concatenate([[0], edges])
    ends = np.concatenate([edges, [len(mask)]])
    for s, e in zip(starts, ends):
        yield bool(mask[s]), int(s), int(e)


def _voice_activity(db: np.ndarray):
    db = np.maximum(db, -90.0)                      # digital silence would drag the floor to -inf
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 97))
    thr = max(floor + 6.0, floor + 0.30 * (peak - floor), peak - 35.0)
    speech = db > thr
    # close short gaps (inter-word / plosives) and drop tiny blips
    hang = int(0.15 / FRAME)
    for val, s, e in list(_runs(speech)):
        if not val and e - s <= hang and s > 0 and e < len(speech):
            speech[s:e] = True
    for val, s, e in list(_runs(speech)):
        if val and e - s < int(0.08 / FRAME):
            speech[s:e] = False
    return speech, floor, peak, thr


def _pitch_track(x: np.ndarray, speech: np.ndarray):
    """Autocorrelation F0 per 40 ms voiced window (75–400 Hz). Returns (times, f0)."""
    win = int(0.04 * SR)
    hop = int(FRAME * SR)
    lo_lag, hi_lag = SR // 400, SR // 75
    times, f0s = [], []
    hann = np.hanning(win).astype(np.float32)
    for i in range(0, len(x) - win, hop):
        fi = i // hop
        if fi >= len(speech) or not speech[fi]:
            continue
        seg = x[i:i + win] * hann
        seg = seg - seg.mean()
        energy = float(np.dot(seg, seg))
        if energy < 1e-6:
            continue
        ac = np.correlate(seg, seg, mode="full")[win - 1:]
        ac = ac / (ac[0] + 1e-9)
        region = ac[lo_lag:hi_lag]
        if not len(region):
            continue
        k = int(np.argmax(region))
        if region[k] < 0.45:            # not clearly periodic → unvoiced
            continue
        lag = k + lo_lag
        # parabolic interpolation for sub-sample lag
        if 0 < k < len(region) - 1:
            a, b, c = region[k - 1], region[k], region[k + 1]
            denom = a - 2 * b + c
            if denom:
                lag = lag + 0.5 * (a - c) / denom
        times.append(i / SR)
        f0s.append(SR / lag)
    return np.array(times), np.array(f0s)


def _band(value, bands):
    for limit, label in bands:
        if value < limit:
            return label
    return bands[-1][1]


def analyze(x: np.ndarray, words: list[dict] | None = None, word_count: int | None = None) -> dict:
    """Full acoustic report. `words` = [{word,start,end}] from a timestamped transcript (optional)."""
    duration = len(x) / SR
    if duration < 1.0:
        raise ValueError("Recording is shorter than one second.")
    fsize = int(FRAME * SR)
    fr = _frames(x, fsize)
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    speech, floor, peak, thr = _voice_activity(db)
    snr = peak - floor
    if not speech.any():
        raise ValueError("No speech detected in the recording — check your microphone.")

    idx = np.flatnonzero(speech)
    t0, t1 = float(idx[0] * FRAME), float((idx[-1] + 1) * FRAME)          # speaking window (trim lead/trail silence)
    window = max(0.1, t1 - t0)
    speech_time = float(speech.sum() * FRAME)

    # ---- pauses (silences inside the speaking window)
    pauses = []
    for val, s, e in _runs(speech):
        st, en = float(s * FRAME), float(e * FRAME)
        if val or st < t0 or en > t1:
            continue
        d = en - st
        if d >= MIN_PAUSE:
            kind = "stall" if d >= STALL else "long" if d >= LONG_PAUSE else "natural"
            pauses.append({"start": round(st, 2), "end": round(en, 2), "duration": round(d, 2), "kind": kind})
    if words:
        for p in pauses:
            before = [w for w in words if w["end"] <= p["start"] + 0.15]
            after = [w for w in words if w["start"] >= p["end"] - 0.15]
            p["after_word"] = before[-1]["word"].strip() if before else None
            p["before_word"] = after[0]["word"].strip() if after else None
            # a pause right after a sentence end is a "strategic" pause, otherwise mid-thought
            p["mid_sentence"] = bool(p["after_word"] and p["after_word"][-1:] not in ".?!,;:")

    minutes = window / 60
    n_words = len(words) if words else (word_count or 0)
    speaking_rate = round(n_words / minutes) if n_words and minutes else None        # incl. pauses
    articulation_rate = round(n_words / (speech_time / 60)) if n_words and speech_time else None

    # ---- pace over time (10 s buckets)
    pace = []
    bucket = 10.0
    b = t0
    while b < t1 - 1:
        e = min(t1, b + bucket)
        if words:
            n = sum(1 for w in words if b <= (w["start"] + w["end"]) / 2 < e)
        else:  # proxy: distribute words by speech time in the bucket
            sb = float(speech[int(b / FRAME):int(e / FRAME)].sum() * FRAME)
            n = n_words * sb / speech_time if speech_time else 0
        pace.append({"t": round(b - t0, 1), "wpm": round(n / ((e - b) / 60)) if e - b > 2 else None})
        b = e
    pace = [p for p in pace if p["wpm"] is not None]
    pace_vals = [p["wpm"] for p in pace]

    # ---- pitch
    pt, f0 = _pitch_track(x, speech)
    pitch = {"voiced_frames": int(len(f0))}
    if len(f0) >= 20:
        med = float(np.median(f0))
        st = 12 * np.log2(f0 / med)
        keep = np.abs(st) < 12                                    # drop octave errors
        st, pt_k = st[keep], pt[keep]
        # light median smoothing
        if len(st) >= 5:
            st = np.array([np.median(st[max(0, i - 2):i + 3]) for i in range(len(st))])
        rng = float(np.percentile(st, 90) - np.percentile(st, 10))
        pitch.update({
            "median_hz": round(med), "range_semitones": round(rng, 1), "std_semitones": round(float(st.std()), 1),
            "label": _band(rng, [(3.5, "monotone"), (6.0, "limited"), (99, "expressive")]),
            "contour": [[round(float(t - t0), 2), round(float(s), 1)] for t, s in
                        zip(pt_k[:: max(1, len(pt_k) // 200)], st[:: max(1, len(st) // 200)])],
        })
    else:
        pitch.update({"label": "unknown", "range_semitones": None})

    # ---- volume
    sp_db = db[speech]
    n_sp = len(sp_db)
    tail = sp_db[int(n_sp * 0.85):] if n_sp > 20 else sp_db
    body = sp_db[int(n_sp * 0.15):int(n_sp * 0.85)] if n_sp > 20 else sp_db
    trail_drop = float(np.mean(body) - np.mean(tail)) if len(tail) and len(body) else 0.0
    clipping = float(np.mean(np.abs(x) > 0.99))

    # ---- waveform envelope for the UI (≈ 400 points over the full recording)
    n_pts = 400
    seg = max(1, len(x) // n_pts)
    env = np.abs(x[: seg * (len(x) // seg)]).reshape(-1, seg).max(axis=1)
    env = env / (env.max() + 1e-9)

    stalls = [p for p in pauses if p["kind"] == "stall"]
    longs = [p for p in pauses if p["kind"] == "long"]
    return {
        "duration": round(duration, 2),
        "speaking_window": [round(t0, 2), round(t1, 2)],
        "window_seconds": round(window, 1),
        "speech_seconds": round(speech_time, 1),
        "silence_ratio": round(1 - speech_time / window, 2),
        "pauses": pauses,
        "pause_count": len(pauses),
        "pauses_per_min": round(len(pauses) / minutes, 1) if minutes else 0,
        "long_pauses": len(longs),
        "stalls": len(stalls),
        "longest_pause": max((p["duration"] for p in pauses), default=0),
        "speaking_rate_wpm": speaking_rate,
        "articulation_rate_wpm": articulation_rate,
        "pace": pace,
        "pace_variability": round(float(np.std(pace_vals)), 1) if len(pace_vals) > 1 else None,
        "pitch": pitch,
        "volume": {"mean_db": round(float(np.mean(sp_db)), 1), "std_db": round(float(np.std(sp_db)), 1),
                   "trailing_drop_db": round(trail_drop, 1), "trails_off": trail_drop > 4.0,
                   "clipping": clipping > 0.001},
        "recording_quality": {"snr_db": round(snr, 1), "ok": snr >= 12,
                              "note": None if snr >= 12 else "Quiet or noisy recording — scores may be less accurate."},
        "envelope": [round(float(v), 3) for v in env],
        "timestamped_words": bool(words),
    }


# --------------------------------------------------------------------------- scoring
def _ramp(v, pts):
    """Piecewise-linear map from value to score; pts = [(value, score), ...] sorted by value."""
    xs, ys = zip(*pts)
    return float(np.interp(v, xs, ys))


def voice_scores(a: dict) -> dict:
    rate = a.get("speaking_rate_wpm") or 0
    pace = _ramp(rate, [(70, 35), (110, 70), (130, 92), (165, 92), (190, 70), (230, 40)]) if rate else None
    pause = 90.0
    pause -= min(30, a["stalls"] * 10) + min(15, a["long_pauses"] * 3)
    if a["window_seconds"] > 25 and a["pauses_per_min"] < 2:
        pause -= 12                                                # no breathing room: rushing
    mid = [p for p in a["pauses"] if p.get("mid_sentence") and p["kind"] != "natural"]
    pause -= min(10, len(mid) * 3)
    rng = a["pitch"].get("range_semitones")
    pitch = _ramp(rng, [(1.5, 30), (3.5, 55), (6, 78), (9, 92), (20, 92)]) if rng is not None else None
    vol = 90.0
    if a["volume"]["trails_off"]:
        vol -= min(20, (a["volume"]["trailing_drop_db"] - 4) * 3 + 8)
    if a["volume"]["std_db"] > 9:
        vol -= 8
    if a["volume"]["clipping"]:
        vol -= 10
    out = {"pace": pace, "pauses": max(20, pause), "pitch_variety": pitch, "volume": max(20, vol)}
    return {k: (None if v is None else int(round(v))) for k, v in out.items()}


def feedback(a: dict, vs: dict) -> tuple[list[str], list[str]]:
    good, fix = [], []
    rate = a.get("speaking_rate_wpm")
    if rate:
        if rate > 185:
            fix.append(f"You spoke at {rate} wpm — fast. Aim for 130–165 and let key points land.")
        elif rate < 105:
            fix.append(f"You spoke at {rate} wpm — slow enough to lose energy. Aim for 130–165.")
        else:
            good.append(f"Comfortable pace: {rate} words per minute.")
    if a["stalls"]:
        where = ", ".join(f"{p['start']:.0f}s" + (f" (after “{p['after_word']}”)" if p.get("after_word") else "")
                          for p in a["pauses"] if p["kind"] == "stall")
        fix.append(f"{a['stalls']} stall(s) over {STALL:.1f}s at {where} — plan your next point before you need it.")
    elif a["window_seconds"] > 25 and a["pauses_per_min"] < 2:
        fix.append("Almost no pauses — you're rushing. Pause for a beat after each key point.")
    elif a["pause_count"]:
        good.append(f"You used {a['pause_count']} pauses without stalling — that sounds deliberate.")
    p = a["pitch"]
    if p.get("label") == "monotone":
        fix.append(f"Pitch range only {p['range_semitones']} semitones — it sounds flat. Lift your voice on the key word of each sentence.")
    elif p.get("label") == "expressive":
        good.append(f"Expressive voice — {p['range_semitones']} semitones of pitch range.")
    if a["volume"]["trails_off"]:
        fix.append(f"Your volume drops {a['volume']['trailing_drop_db']} dB at the end — finish your last sentence as strongly as the first.")
    return good, fix
