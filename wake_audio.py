"""Android side of the personal wake word: record the name, calibrate, test. No service yet.

Needs java/org/jarvis/jarvis/WakeCore.java in the build (buildozer.spec: android.add_src = java) and the
RECORD_AUDIO permission. Everything here is blocking: call it from a worker thread, never from the UI thread.
NOT tested on a real phone yet (WakeCore itself is tested on a PC).
"""
import os

SR = 16000
CODES = {0: "ok", 1: "too_short", 2: "too_long", 3: "no_speech"}


def _core_class():
    try:
        from jnius import autoclass
        return autoclass("org.jarvis.jarvis.WakeCore")
    except Exception:
        return None


def available():
    """False when the APK was built without WakeCore.java (the feature must then stay hidden)."""
    return _core_class() is not None


def record(seconds):
    """Records `seconds` from the microphone. Returns (java short[] array, sample count)."""
    from jnius import autoclass
    AudioRecord = autoclass("android.media.AudioRecord")
    AudioFormat = autoclass("android.media.AudioFormat")
    Array = autoclass("java.lang.reflect.Array")
    Short = autoclass("java.lang.Short")
    total = int(seconds * SR)
    buf = Array.newInstance(Short.TYPE, total)
    min_buf = AudioRecord.getMinBufferSize(SR, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
    rec = AudioRecord(1, SR, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(int(min_buf), 8192))
    if rec.getState() != AudioRecord.STATE_INITIALIZED:
        rec.release()
        raise RuntimeError("microphone_unavailable")
    got = 0
    try:
        rec.startRecording()
        while got < total:
            n = rec.read(buf, got, min(1600, total - got))
            if n <= 0:
                break
            got += n
    finally:
        try:
            rec.stop()
        except Exception:
            pass
        rec.release()
    return buf, got


class WakeModel(object):
    """One personal wake-word model: enroll, save, load, test."""

    def __init__(self, user_data_dir):
        self.path = os.path.join(user_data_dir, "wake_model.txt")
        self.Core = _core_class()
        self.core = None

    # ----- storage -----
    def load(self):
        if self.Core is None or not os.path.isfile(self.path):
            return False
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                core = self.Core.deserialize(fh.read())
            if core is None:
                return False
            self.core = core
            return bool(core.isReady())
        except Exception:
            return False

    def save(self):
        if self.core is not None:
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write(str(self.core.serialize()))

    def delete(self):
        try:
            os.remove(self.path)
        except OSError:
            pass
        self.core = None

    # ----- enrollment (call in this order) -----
    def start_enrollment(self):
        self.core = self.Core()

    def add_name(self, seconds=2.5):
        """Record the name once. Returns 'ok', 'too_short', 'too_long' or 'no_speech'."""
        buf, n = record(seconds)
        return CODES.get(int(self.core.addTemplate(buf, n)), "no_speech")

    def add_other_speech(self, seconds=15):
        """Record normal talk (not the name). Returns how many speech pieces were stored."""
        buf, n = record(seconds)
        return int(self.core.addNegative(buf, n))

    def finish(self):
        """Needs at least 3 recordings of the name. Returns a small report for the screen."""
        ok = bool(self.core.finish())
        return {
            "ok": ok,
            "names": int(self.core.templateCount()),
            "others": int(self.core.negativeCount()),
            "separable": bool(self.core.isSeparable()),
            "margin": float(self.core.getMargin()),
            "threshold": float(self.core.getThreshold()),
            "fit": [float(v) for v in self.core.looScores()],
        }

    def drop_name(self, index):
        self.core.removeTemplate(int(index))

    # ----- test and settings -----
    def test(self, seconds=3):
        """Say the name (or something else) once. Returns (score, triggered). Lower score = closer to the name."""
        buf, n = record(seconds)
        score = float(self.core.scoreClip(buf, n))
        return score, bool(self.core.wouldTrigger(buf, n))

    def set_sensitivity(self, value):
        self.core.setSensitivity(float(value))
        self.save()
