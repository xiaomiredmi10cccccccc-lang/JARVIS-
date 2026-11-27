package org.jarvis.jarvis;

import java.util.ArrayList;
import java.util.List;

/**
 * Personal wake word: the user records the assistant's name a few times, the phone keeps only
 * small feature sequences (MFCC) and later recognises the same word by time-warped matching (DTW).
 * No audio leaves the phone. No Android imports on purpose: the same file is tested on a PC.
 *
 * Audio format everywhere: 16 kHz, mono, 16-bit PCM (short[]).
 *
 * Enrollment:  addTemplate() x 3..6   ->  addNegative() with other speech (optional, recommended)  ->  finish()
 * Detection:   feed() with live audio, or scoreClip() for a "test" button.
 * Storage:     serialize() / deserialize() (plain text, templates + threshold).
 */
public class WakeCore {

    public static final int SR = 16000;
    static final int FRAME = 400;          // 25 ms
    static final int HOP = 160;            // 10 ms
    static final int NFFT = 512;
    static final int NMEL = 26;
    static final int NCEP = 12;            // c1..c12
    static final int DIM = NCEP * 2;       // + deltas
    static final float DELTA_W = 1.0f;
    static final float INF = 1e9f;
    static final int WARM_FRAMES = 20;     // 0.2 s to measure the room noise when listening starts
    static final int[] SHIFTS = {0, 4, 8, 14, 20, 30};   // the VAD may start a little early: try later starts too

    // template length limits (frames of 10 ms)
    static final int MIN_FRAMES = 20;      // 0.2 s
    static final int MAX_FRAMES = 200;     // 2.0 s

    // enrollment status codes returned by addTemplate()
    public static final int OK = 0;
    public static final int TOO_SHORT = 1;
    public static final int TOO_LONG = 2;
    public static final int NO_SPEECH = 3;

    // ---------- static tables ----------
    private static final float[] WIN = new float[FRAME];
    private static final float[][] MEL = new float[NMEL][NFFT / 2 + 1];
    private static final float[][] DCT = new float[NCEP][NMEL];
    private static final float[] COS = new float[NFFT / 2];
    private static final float[] SIN = new float[NFFT / 2];
    private static final int[] REV = new int[NFFT];

    static {
        for (int i = 0; i < FRAME; i++) WIN[i] = (float) (0.54 - 0.46 * Math.cos(2 * Math.PI * i / (FRAME - 1)));
        for (int i = 0; i < NFFT / 2; i++) {
            COS[i] = (float) Math.cos(-2 * Math.PI * i / NFFT);
            SIN[i] = (float) Math.sin(-2 * Math.PI * i / NFFT);
        }
        int bits = Integer.numberOfTrailingZeros(NFFT);
        for (int i = 0; i < NFFT; i++) REV[i] = Integer.reverse(i) >>> (32 - bits);
        double lo = mel(100), hi = mel(7600);
        double[] pts = new double[NMEL + 2];
        for (int i = 0; i < pts.length; i++) pts[i] = invMel(lo + (hi - lo) * i / (NMEL + 1));
        for (int m = 0; m < NMEL; m++) {
            double a = pts[m], b = pts[m + 1], c = pts[m + 2];
            for (int k = 0; k <= NFFT / 2; k++) {
                double f = (double) k * SR / NFFT;
                double w = 0;
                if (f > a && f < b) w = (f - a) / (b - a);
                else if (f >= b && f < c) w = (c - f) / (c - b);
                MEL[m][k] = (float) w;
            }
        }
        for (int c = 0; c < NCEP; c++)
            for (int m = 0; m < NMEL; m++)
                DCT[c][m] = (float) Math.cos(Math.PI * (c + 1) * (m + 0.5) / NMEL);
    }

    private static double mel(double f) { return 2595.0 * Math.log10(1.0 + f / 700.0); }
    private static double invMel(double m) { return 700.0 * (Math.pow(10.0, m / 2595.0) - 1.0); }

    // ---------- model state ----------
    private final List<float[][]> tplRaw = new ArrayList<float[][]>();    // trimmed raw cepstra of the name
    private final List<float[][]> tplNorm = new ArrayList<float[][]>();   // normalised (CMN + deltas)
    private final List<float[][]> negRaw = new ArrayList<float[][]>();    // speech segments of other talk
    private float threshold = 0f;
    private float margin = 0f;
    private float sensitivity = 0.5f;
    private boolean separable = false;
    private boolean ready = false;
    private float lastScore = INF;
    private float levelDb = -90f;

    // ---------- streaming state ----------
    private float[] pend = new float[0];
    private float noiseFloor = -60f;
    private int warm = 0;                       // frames seen since the stream started (first 20 calibrate the noise floor)
    private final float[] warmE = new float[WARM_FRAMES];
    private boolean inSpeech = false;
    private int startCount = 0;
    private int quiet = 0;
    private boolean earlyChecked = false;
    private int refractory = 0;
    private final List<float[]> hist = new ArrayList<float[]>();
    private List<float[]> seg = new ArrayList<float[]>();

    // =====================================================================================
    // public API
    // =====================================================================================

    public synchronized void reset() {
        tplRaw.clear(); tplNorm.clear(); negRaw.clear();
        threshold = 0f; margin = 0f; separable = false; ready = false; lastScore = INF;
        resetStream();
    }

    /** Add one recording of the name. Returns OK or a reason code. */
    public synchronized int addTemplate(short[] pcm, int n) {
        float[] x = toFloat(pcm, n);
        if (x.length < FRAME * 3) return TOO_SHORT;
        Feats f = features(x);
        int[] r = speechRange(f.e);
        if (r == null) return NO_SPEECH;
        int len = r[1] - r[0] + 1;
        if (len < MIN_FRAMES) return TOO_SHORT;
        if (len > MAX_FRAMES) return TOO_LONG;
        float mean = 0;
        for (int t = r[0]; t <= r[1]; t++) mean += f.e[t];
        mean /= len;
        if (mean < -50f) return NO_SPEECH;
        float[][] raw = new float[len][];
        for (int t = 0; t < len; t++) raw[t] = f.c[r[0] + t];
        tplRaw.add(raw);
        tplNorm.add(normalize(raw, raw.length, raw.length));
        ready = false;
        return OK;
    }

    public synchronized int templateCount() { return tplRaw.size(); }

    /** Add other speech/noise (about 10-20 s of normal talk). Returns how many segments were stored. */
    public synchronized int addNegative(short[] pcm, int n) {
        float[] x = toFloat(pcm, n);
        if (x.length < FRAME * 3) return 0;
        Feats f = features(x);
        List<int[]> segs = segments(f.e);
        int added = 0;
        for (int[] s : segs) {
            int len = s[1] - s[0] + 1;
            if (len < 15) continue;
            int use = Math.min(len, 250);
            float[][] raw = new float[use][];
            for (int t = 0; t < use; t++) raw[t] = f.c[s[0] + t];
            negRaw.add(raw);
            added++;
        }
        return added;
    }

    public synchronized int negativeCount() { return negRaw.size(); }

    /** Calibrate the threshold. Needs at least 3 templates. */
    public synchronized boolean finish() {
        int k = tplRaw.size();
        if (k < 3) return false;
        float posMax = 0f, posSum = 0f;
        for (int i = 0; i < k; i++) {
            float best = INF;
            for (int j = 0; j < k; j++) {
                if (j == i) continue;
                float d = dtw(tplNorm.get(j), normalizeCandidate(tplRaw.get(i), tplRaw.get(j).length), true);
                if (d < best) best = d;
            }
            posMax = Math.max(posMax, best);
            posSum += best;
        }
        float posMean = posSum / k;
        float negMin = INF;
        for (float[][] nraw : negRaw) {
            float d = candidateScore(nraw);
            if (d < negMin) negMin = d;
        }
        if (negRaw.size() >= 5 && negMin < INF) {
            if (negMin > posMax) {
                threshold = posMax + 0.45f * (negMin - posMax);
                separable = true;
            } else {
                threshold = 0.5f * (posMean + negMin);
                separable = false;
            }
            margin = (negMin - posMax) / Math.max(posMax, 1e-3f);
        } else {
            threshold = posMax * 1.3f;
            separable = false;
            margin = 0f;
        }
        ready = true;
        return true;
    }

    public synchronized boolean isReady() { return ready; }
    public synchronized boolean isSeparable() { return separable; }
    public synchronized float getThreshold() { return threshold; }
    public synchronized float getMargin() { return margin; }
    public synchronized float getLastScore() { return lastScore; }
    public synchronized float getLevelDb() { return levelDb; }

    /** 0 = fewer false triggers, 1 = easier to trigger. Default 0.5. */
    public synchronized void setSensitivity(float s) { sensitivity = Math.max(0f, Math.min(1f, s)); }
    public synchronized float getSensitivity() { return sensitivity; }

    /** How well each recording fits the others (lower is better). Large values = a bad recording. */
    public synchronized float[] looScores() {
        int k = tplRaw.size();
        float[] out = new float[k];
        for (int i = 0; i < k; i++) {
            float best = INF;
            for (int j = 0; j < k; j++) {
                if (j == i) continue;
                float d = dtw(tplNorm.get(j), normalizeCandidate(tplRaw.get(i), tplRaw.get(j).length), true);
                if (d < best) best = d;
            }
            out[i] = best;
        }
        return out;
    }

    /** Remove one template (for example a bad recording). Call finish() again afterwards. */
    public synchronized void removeTemplate(int index) {
        if (index >= 0 && index < tplRaw.size()) {
            tplRaw.remove(index);
            tplNorm.remove(index);
            ready = false;
        }
    }

    /** Test button: best score in a clip (lower = closer to the name). 999 if no speech. */
    public synchronized float scoreClip(short[] pcm, int n) {
        float[] x = toFloat(pcm, n);
        if (x.length < FRAME * 3 || tplRaw.isEmpty()) return 999f;
        Feats f = features(x);
        float best = INF;
        for (int[] s : segments(f.e)) {
            int len = s[1] - s[0] + 1;
            if (len < 15) continue;
            int use = Math.min(len, 300);
            float[][] raw = new float[use][];
            for (int t = 0; t < use; t++) raw[t] = f.c[s[0] + t];
            float d = candidateScore(raw);
            if (d < best) best = d;
        }
        lastScore = best >= INF ? 999f : best;
        return lastScore;
    }

    /** True when this clip would trigger the wake word. */
    public synchronized boolean wouldTrigger(short[] pcm, int n) {
        float sc = scoreClip(pcm, n);
        return ready && sc <= effectiveThreshold();
    }

    /** Live audio. Returns true when the wake word was heard in the audio fed so far. */
    public synchronized boolean feed(short[] pcm, int n) {
        if (!ready || tplRaw.isEmpty()) return false;
        float[] add = toFloat(pcm, n);
        float[] all = new float[pend.length + add.length];
        System.arraycopy(pend, 0, all, 0, pend.length);
        System.arraycopy(add, 0, all, pend.length, add.length);
        int pos = 0;
        boolean fired = false;
        float[] frame = new float[FRAME];
        while (all.length - pos >= FRAME) {
            System.arraycopy(all, pos, frame, 0, FRAME);
            pos += HOP;
            float e = frameEnergyDb(frame);
            float[] c = frameCep(frame);
            levelDb = e;
            if (processFrame(e, c)) fired = true;
        }
        float[] rest = new float[all.length - pos];
        System.arraycopy(all, pos, rest, 0, rest.length);
        pend = rest;
        return fired;
    }

    public synchronized void resetStream() {
        pend = new float[0];
        noiseFloor = -60f; warm = 0; inSpeech = false; startCount = 0; quiet = 0; earlyChecked = false;
        refractory = 0; hist.clear(); seg = new ArrayList<float[]>();
    }

    // ---------- storage ----------

    public synchronized String serialize() {
        StringBuilder sb = new StringBuilder();
        sb.append("WAKE1\n");
        sb.append(threshold).append(',').append(margin).append(',').append(sensitivity).append(',')
          .append(separable ? 1 : 0).append(',').append(ready ? 1 : 0).append('\n');
        sb.append(tplRaw.size()).append('\n');
        for (float[][] t : tplRaw) {
            sb.append(t.length).append('\n');
            for (float[] row : t) {
                for (int d = 0; d < NCEP; d++) {
                    if (d > 0) sb.append(',');
                    sb.append(row[d]);
                }
                sb.append('\n');
            }
        }
        return sb.toString();
    }

    /** Returns null if the text is not a valid saved model. */
    public static WakeCore deserialize(String s) {
        try {
            String[] lines = s.split("\n");
            int p = 0;
            if (!lines[p++].trim().equals("WAKE1")) return null;
            String[] h = lines[p++].trim().split(",");
            WakeCore w = new WakeCore();
            w.threshold = Float.parseFloat(h[0]);
            w.margin = Float.parseFloat(h[1]);
            w.sensitivity = Float.parseFloat(h[2]);
            w.separable = h[3].equals("1");
            boolean wasReady = h[4].equals("1");
            int k = Integer.parseInt(lines[p++].trim());
            for (int i = 0; i < k; i++) {
                int len = Integer.parseInt(lines[p++].trim());
                float[][] raw = new float[len][NCEP];
                for (int t = 0; t < len; t++) {
                    String[] v = lines[p++].trim().split(",");
                    for (int d = 0; d < NCEP; d++) raw[t][d] = Float.parseFloat(v[d]);
                }
                w.tplRaw.add(raw);
                w.tplNorm.add(normalize(raw, raw.length, raw.length));
            }
            w.ready = wasReady && k >= 3;
            return w;
        } catch (Exception e) {
            return null;
        }
    }

    // =====================================================================================
    // detection internals
    // =====================================================================================

    private float effectiveThreshold() {
        return threshold * (0.8f + 0.4f * sensitivity);
    }

    /** Returns true if this frame completed a detection. */
    private boolean processFrame(float e, float[] c) {
        hist.add(c);
        if (hist.size() > 12) hist.remove(0);
        if (warm < WARM_FRAMES) {
            warmE[warm++] = e;
            if (warm == WARM_FRAMES) {
                float[] t = warmE.clone();
                java.util.Arrays.sort(t);
                noiseFloor = Math.max(-75f, Math.min(-30f, t[WARM_FRAMES / 2]));
            }
            return false;
        }
        if (!inSpeech && e < noiseFloor + 12f) {
            // follow the room noise: quickly down, slowly up
            noiseFloor += (e < noiseFloor ? 0.2f : 0.01f) * (e - noiseFloor);
            if (noiseFloor < -75f) noiseFloor = -75f;
            if (noiseFloor > -30f) noiseFloor = -30f;
        }
        float thr = Math.max(noiseFloor + 9f, -52f);
        boolean loud = e > thr;
        if (refractory > 0) refractory--;

        if (!inSpeech) {
            startCount = loud ? startCount + 1 : 0;
            if (startCount >= 3) {
                inSpeech = true;
                quiet = 0;
                earlyChecked = false;
                seg = new ArrayList<float[]>();
                int take = Math.min(hist.size(), 11);
                for (int i = hist.size() - take; i < hist.size(); i++) seg.add(hist.get(i));
            }
            return false;
        }

        seg.add(c);
        quiet = loud ? 0 : quiet + 1;
        boolean fired = false;
        if (!earlyChecked && seg.size() >= 150) {
            earlyChecked = true;
            fired = evaluate(seg, 150);
        }
        if (!fired && (quiet >= 25 || seg.size() >= 300)) {
            int end = seg.size() - Math.max(0, quiet - 3);
            fired = evaluate(seg, end);
            inSpeech = false; startCount = 0; quiet = 0; seg = new ArrayList<float[]>();
        } else if (fired) {
            inSpeech = false; startCount = 0; quiet = 0; seg = new ArrayList<float[]>();
        }
        return fired;
    }

    private boolean evaluate(List<float[]> s, int upto) {
        int len = Math.min(s.size(), upto);
        if (len < 15 || refractory > 0) return false;
        int use = Math.min(len, 300);
        float[][] raw = new float[use][];
        for (int i = 0; i < use; i++) raw[i] = s.get(i);
        float sc = candidateScore(raw);
        lastScore = sc;
        if (sc <= effectiveThreshold()) {
            refractory = 150;
            return true;
        }
        return false;
    }

    /** Best (smallest) open-end DTW distance of the candidate start against all templates (several start shifts). */
    private float candidateScore(float[][] raw) {
        float best = INF;
        for (int sh : SHIFTS) {
            if (raw.length - sh < 15) break;
            float[][] part = sh == 0 ? raw : java.util.Arrays.copyOfRange(raw, sh, raw.length);
            for (int j = 0; j < tplRaw.size(); j++) {
                float[][] cand = normalizeCandidate(part, tplRaw.get(j).length);
                float d = dtw(tplNorm.get(j), cand, true);
                if (d < best) best = d;
            }
        }
        return best;
    }

    // =====================================================================================
    // features
    // =====================================================================================

    private static final class Feats {
        float[][] c;
        float[] e;
    }

    private static float[] toFloat(short[] pcm, int n) {
        int m = Math.max(0, Math.min(n, pcm.length));
        float[] x = new float[m];
        for (int i = 0; i < m; i++) x[i] = pcm[i] / 32768f;
        return x;
    }

    private static Feats features(float[] x) {
        int nf = (x.length - FRAME) / HOP + 1;
        Feats f = new Feats();
        f.c = new float[Math.max(nf, 0)][];
        f.e = new float[Math.max(nf, 0)];
        float[] frame = new float[FRAME];
        for (int t = 0; t < nf; t++) {
            System.arraycopy(x, t * HOP, frame, 0, FRAME);
            f.e[t] = frameEnergyDb(frame);
            f.c[t] = frameCep(frame);
        }
        return f;
    }

    private static float frameEnergyDb(float[] fr) {
        double s = 0;
        for (int i = 0; i < FRAME; i++) s += (double) fr[i] * fr[i];
        return (float) (10.0 * Math.log10(s / FRAME + 1e-9));
    }

    private static float[] frameCep(float[] fr) {
        float[] re = new float[NFFT];
        float[] im = new float[NFFT];
        float prev = 0f;
        for (int i = 0; i < FRAME; i++) {
            float v = fr[i] - 0.97f * prev;
            prev = fr[i];
            re[REV[i]] = v * WIN[i];
        }
        fft(re, im);
        int half = NFFT / 2 + 1;
        float[] pw = new float[half];
        for (int k = 0; k < half; k++) pw[k] = re[k] * re[k] + im[k] * im[k];
        float[] lm = new float[NMEL];
        for (int m = 0; m < NMEL; m++) {
            double acc = 0;
            float[] w = MEL[m];
            for (int k = 0; k < half; k++) acc += w[k] * pw[k];
            lm[m] = (float) Math.log(Math.max(acc, 1e-10));
        }
        float[] cep = new float[NCEP];
        for (int c = 0; c < NCEP; c++) {
            double acc = 0;
            for (int m = 0; m < NMEL; m++) acc += DCT[c][m] * lm[m];
            cep[c] = (float) acc;
        }
        return cep;
    }

    /** In-place radix-2 FFT; input already placed in bit-reversed order. */
    private static void fft(float[] re, float[] im) {
        for (int size = 2; size <= NFFT; size <<= 1) {
            int halfsize = size >> 1;
            int step = NFFT / size;
            for (int i = 0; i < NFFT; i += size) {
                for (int j = i, k = 0; j < i + halfsize; j++, k += step) {
                    float tr = re[j + halfsize] * COS[k] - im[j + halfsize] * SIN[k];
                    float ti = re[j + halfsize] * SIN[k] + im[j + halfsize] * COS[k];
                    re[j + halfsize] = re[j] - tr;
                    im[j + halfsize] = im[j] - ti;
                    re[j] += tr;
                    im[j] += ti;
                }
            }
        }
    }

    // ---------- voice activity ----------

    private static float percentile(float[] v, double p) {
        float[] c = v.clone();
        java.util.Arrays.sort(c);
        int i = (int) Math.max(0, Math.min(c.length - 1, Math.round(p * (c.length - 1))));
        return c[i];
    }

    /** Offline: the main speech region of a short clip, or null. */
    private static int[] speechRange(float[] e) {
        if (e.length < 10) return null;
        float noise = percentile(e, 0.10);
        float thr = Math.max(noise + 9f, -52f);
        int first = -1, last = -1, run = 0;
        for (int t = 0; t < e.length; t++) {
            if (e[t] > thr) {
                run++;
                if (run >= 3 && first < 0) first = t - 2;
            } else run = 0;
        }
        run = 0;
        for (int t = e.length - 1; t >= 0; t--) {
            if (e[t] > thr) {
                run++;
                if (run >= 3 && last < 0) last = t + 2;
            } else run = 0;
        }
        if (first < 0 || last < 0 || last <= first) return null;
        first = Math.max(0, first - 4);
        last = Math.min(e.length - 1, last + 4);
        return new int[]{first, last};
    }

    /** Offline: all speech segments of a longer clip (pauses of 250 ms split them). */
    private static List<int[]> segments(float[] e) {
        List<int[]> out = new ArrayList<int[]>();
        if (e.length < 10) return out;
        float noise = percentile(e, 0.10);
        float thr = Math.max(noise + 9f, -52f);
        int start = -1, quiet = 0, run = 0;
        for (int t = 0; t < e.length; t++) {
            boolean loud = e[t] > thr;
            if (start < 0) {
                run = loud ? run + 1 : 0;
                if (run >= 3) { start = Math.max(0, t - 2 - 8); quiet = 0; }
            } else {
                quiet = loud ? 0 : quiet + 1;
                if (quiet >= 25) {
                    out.add(new int[]{start, t - quiet + 3});
                    start = -1; run = 0;
                }
            }
        }
        if (start >= 0) out.add(new int[]{start, e.length - 1});
        return out;
    }

    // ---------- normalisation and matching ----------

    /** Cepstral mean removal over the first meanFrames frames, then deltas. Uses the first count frames. */
    private static float[][] normalize(float[][] raw, int meanFrames, int count) {
        int n = Math.min(count, raw.length);
        int mf = Math.max(1, Math.min(meanFrames, raw.length));
        float[] mean = new float[NCEP];
        for (int t = 0; t < mf; t++)
            for (int d = 0; d < NCEP; d++) mean[d] += raw[t][d];
        for (int d = 0; d < NCEP; d++) mean[d] /= mf;
        float[][] cm = new float[n][NCEP];
        for (int t = 0; t < n; t++)
            for (int d = 0; d < NCEP; d++) cm[t][d] = raw[t][d] - mean[d];
        float[][] out = new float[n][DIM];
        for (int t = 0; t < n; t++) {
            int t1 = Math.min(n - 1, t + 1), t2 = Math.min(n - 1, t + 2);
            int m1 = Math.max(0, t - 1), m2 = Math.max(0, t - 2);
            for (int d = 0; d < NCEP; d++) {
                out[t][d] = cm[t][d];
                out[t][NCEP + d] = DELTA_W * ((cm[t1][d] - cm[m1][d]) + 2f * (cm[t2][d] - cm[m2][d])) / 10f * 3f;
            }
        }
        return out;
    }

    private static float[][] normalizeCandidate(float[][] raw, int templateLen) {
        return normalize(raw, templateLen, templateLen * 2 + 4);
    }

    /**
     * Symmetric DTW with slope limits. a = template, b = candidate.
     * openEnd: the template must match the START of the candidate, the candidate may go on afterwards.
     * Returns a length-normalised distance.
     */
    private static float dtw(float[][] a, float[][] b, boolean openEnd) {
        int T = a.length, J = b.length;
        if (T == 0 || J == 0) return INF;
        float[] prev = new float[J];
        float[] cur = new float[J];
        float best = INF;
        for (int i = 0; i < T; i++) {
            for (int j = 0; j < J; j++) {
                if (!allowed(i, j)) { cur[j] = INF; continue; }
                float d = dist(a[i], b[j]);
                float v;
                if (i == 0 && j == 0) v = 2f * d;
                else {
                    float m = INF;
                    if (i > 0 && j > 0) m = Math.min(m, prev[j - 1] + 2f * d);
                    if (i > 0) m = Math.min(m, prev[j] + d);
                    if (j > 0) m = Math.min(m, cur[j - 1] + d);
                    v = m;
                }
                cur[j] = v >= INF ? INF : v;
            }
            if (i == T - 1) {
                if (openEnd) {
                    for (int j = 0; j < J; j++)
                        if (cur[j] < INF) best = Math.min(best, cur[j] / (T + j + 1));
                } else if (cur[J - 1] < INF) {
                    best = cur[J - 1] / (T + J);
                }
            }
            float[] tmp = prev; prev = cur; cur = tmp;
        }
        return best;
    }

    private static boolean allowed(int i, int j) {
        return (j + 1) <= 2 * (i + 1) + 2 && (i + 1) <= 2 * (j + 1) + 2;
    }

    private static float dist(float[] x, float[] y) {
        float s = 0f;
        for (int k = 0; k < DIM; k++) {
            float d = x[k] - y[k];
            s += d * d;
        }
        return (float) Math.sqrt(s);
    }
}
