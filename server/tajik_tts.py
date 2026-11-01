"""Tajik voice for the JARVIS server: Meta MMS-TTS (facebook/mms-tts-tgk) exported to ONNX, run with onnxruntime.

Set the environment variable TAJIK_TTS_REPO on Render to the public Hugging Face model repo that holds
model.onnx, vocab.json and config.json (for example "your-name/mms-tts-tgk-onnx"). Without it the Tajik voice is
simply not available and the app keeps using the phone voice. No token or key is needed or stored.
Model license: CC-BY-NC 4.0 (non-commercial use only).
"""
import io
import json
import os
import tempfile
import threading
import urllib.request
import wave

import numpy as np

from tajik_text import normalize, split_sentences

REPO = os.environ.get("TAJIK_TTS_REPO", "").strip()
FILES = ("model.onnx", "vocab.json", "config.json")
MAX_CHARS = 1500

_state = {"session": None, "vocab": None, "rate": 16000, "pad": 0, "error": None, "loading": False}
_lock = threading.Lock()
_run_lock = threading.Lock()      # one synthesis at a time keeps memory low on a small server


class TajikVoiceNotReady(Exception):
    pass


def available():
    return bool(REPO)


def _download(name, folder):
    path = os.path.join(folder, name)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    url = "https://huggingface.co/%s/resolve/main/%s" % (REPO, name)
    tmp = path + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-server"})
    with urllib.request.urlopen(req, timeout=120) as response, open(tmp, "wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    os.replace(tmp, path)
    return path


def _load():
    try:
        import onnxruntime as ort
        folder = os.path.join(tempfile.gettempdir(), "jarvis_tajik_tts")
        os.makedirs(folder, exist_ok=True)
        model_path = _download("model.onnx", folder)
        with open(_download("vocab.json", folder), "r", encoding="utf-8") as fh:
            vocab = json.load(fh)
        try:
            with open(_download("config.json", folder), "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            rate = int(cfg.get("sampling_rate", 16000))
            pad = int(cfg.get("pad_id", vocab.get("<pad>", vocab.get("_", 0))))
        except Exception:
            rate, pad = 16000, vocab.get("<pad>", vocab.get("_", 0))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC   # lower memory use
        session = ort.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])
        _state.update(session=session, vocab=vocab, rate=rate, pad=pad, error=None)
        print("Tajik voice loaded")
    except Exception as exc:
        _state["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        print("Tajik voice failed to load:", _state["error"])
    finally:
        _state["loading"] = False


def start_loading():
    """Start downloading/loading in the background (call once at server start)."""
    with _lock:
        if not REPO:
            print("Tajik voice: TAJIK_TTS_REPO is not set, voice is off")
            return
        if _state["session"] is not None or _state["loading"]:
            return
        _state["loading"] = True
    print("Tajik voice: downloading/loading model from", REPO)
    threading.Thread(target=_load, daemon=True).start()


def encode(text, vocab, pad=0):
    """Same as the Hugging Face VitsTokenizer: characters, with the pad (blank) id between all of them."""
    chars = [c for c in text if c in vocab]
    ids = [pad] * (len(chars) * 2 + 1)
    ids[1::2] = [vocab[c] for c in chars]
    return ids


def synthesize(text):
    """Return WAV bytes for Tajik text. Raises TajikVoiceNotReady while the model is still loading."""
    if not REPO:
        raise TajikVoiceNotReady("TAJIK_TTS_REPO is not set")
    session, vocab = _state["session"], _state["vocab"]
    if session is None:
        start_loading()
        raise TajikVoiceNotReady(_state["error"] or "loading")
    rate = _state["rate"]
    gap = np.zeros(int(rate * 0.25), dtype=np.float32)
    chunks = []
    with _run_lock:
        for piece in split_sentences(text.strip()[:MAX_CHARS]):
            cleaned = normalize(piece, set(vocab.keys()))
            if not cleaned:
                continue
            ids = np.array([encode(cleaned, vocab, _state["pad"])], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": np.ones_like(ids)}
            names = {i.name for i in session.get_inputs()}
            feed = {k: v for k, v in feed.items() if k in names}
            wave_out = session.run(None, feed)[0]
            audio = np.asarray(wave_out, dtype=np.float32).reshape(-1)
            if audio.size:
                chunks.append(audio)
                chunks.append(gap)
    if not chunks:
        raise ValueError("nothing to read")
    pcm = (np.clip(np.concatenate(chunks), -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


start_loading()
