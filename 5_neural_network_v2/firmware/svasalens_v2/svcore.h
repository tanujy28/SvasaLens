// SvasaLens V2 core: signal steps + the two networks.
// Plain C++ with no Arduino calls, so the same file runs on the XIAO and on a laptop for testing.
// Every step copies the Python code the networks were trained and tested with:
//   svdet.bandpass, svdetnn.normalise, svdetnn.DetNet, svdetnn.detect (svdet.runs),
//   svnn.chip_filter, svnn.window and the NN v2 typing network.
#pragma once
#include <math.h>
#include <string.h>
#include <stdint.h>
#include "svweights.h"

namespace sv {

const int FS = 8;                       // samples per second

// ---------------------------------------------------------------- filters
// One biquad, same form as scipy sosfilt / lfilter (transposed direct form II), in double.
struct Biquad {
  double b0, b1, b2, a1, a2, z0 = 0, z1 = 0;
  double step(double x) { double y = b0 * x + z0; z0 = b1 * x - a1 * y + z1; z1 = b2 * x - a2 * y; return y; }
};
// svdet.bandpass: butter(2, [0.05, 1] Hz, band) at 8 Hz = two biquads (used for the detection network)
struct Bandpass {
  Biquad s1{0.0897568423096922, 0.1795136846193844, 0.0897568423096922, -0.9991872687996722, 0.3712244388403171};
  Biquad s2{1.0, -2.0, 1.0, -1.9448635279466466, 0.946503965641721};
  double step(double x) { return s2.step(s1.step(x)); }
};
// svnn.chip_filter: high-pass 0.1 Hz then low-pass 0.5 Hz (used for the typing network)
struct ChipFilter {
  Biquad hp{0.94597685600279, -1.89195371200558, 0.94597685600279, -1.8890330793945245, 0.8948743446166355};
  Biquad lp{0.029954582208092474, 0.05990916441618495, 0.029954582208092474, -1.454243586251585, 0.5740619150839548};
  double step(double x) { return lp.step(hp.step(x)); }
};

// ---------------------------------------------------------------- normalisation (svdetnn.normalise)
// Breathing size = RMS of the last 6 s, one value per second. Each second's samples are divided by the
// 90th percentile of the previous 120 per-second sizes (second 0 uses its own size, as in Python), clipped to +-10.
struct Normaliser {
  static const int ENV_N = 6 * FS, BASE_S = 120;
  float last[ENV_N]; int nlast = 0, lpos = 0;   // last 48 filtered samples
  float env[BASE_S]; int nenv = 0, epos = 0;     // last 120 per-second sizes
  float env0 = 0;
  // base for the second about to be divided, given the sizes so far
  float base() const {
    float v[BASE_S];
    int n = 0;
    int pad = BASE_S - nenv;                   // seconds before the start are filled with the first size
    for (int i = 0; i < pad; i++) v[n++] = env0;
    for (int i = 0; i < nenv; i++) v[n++] = env[(epos - nenv + i + BASE_S) % BASE_S];
    // sort (insertion sort, 120 values once per second)
    for (int i = 1; i < BASE_S; i++) { float t = v[i]; int j = i - 1; while (j >= 0 && v[j] > t) { v[j + 1] = v[j]; j--; } v[j + 1] = t; }
    double pos = (BASE_S - 1) * 0.90; int lo = (int)pos; double fr = pos - lo;       // numpy 'linear' percentile
    double p = v[lo] + fr * ((double)v[lo + 1] - v[lo]);
    return (float)p;
  }
  // x: the 8 filtered samples of this second. Writes the network inputs to out.
  void second(const float *x, float *out) {
    for (int k = 0; k < FS; k++) { last[lpos] = x[k]; lpos = (lpos + 1) % ENV_N; if (nlast < ENV_N) nlast++; }
    double s = 0;
    for (int i = 0; i < nlast; i++) { double v = last[i]; s += v * v; }
    float e = (float)sqrt(s / (nlast > 0 ? nlast : 1));
    if (!started) { env0 = e; started = true; }   // second 0: Python pads the past with this first size
    float b = base();
    if (b < 1e-6f) b = 1e-6f;
    for (int k = 0; k < FS; k++) { float v = x[k] / b; out[k] = v > 10 ? 10 : (v < -10 ? -10 : v); }
    env[epos] = e; epos = (epos + 1) % BASE_S; if (nenv < BASE_S) nenv++;
  }
  bool started = false;
};

// ---------------------------------------------------------------- detection network (svdetnn.DetNet)
// 7 causal dilated convolutions (kernel 9, dilations 1..64) + ReLU, then a 1x1 head.
// Runs one sample at a time; each layer keeps just enough past inputs.
struct DetNet {
  static const int K = 9, L = 7, CH = 16;
  float *hist[L]; int hlen[L], hpos[L], cin[L], dil[L];
  float buf[CH], buf2[CH];
  bool init() {
    int d = 1;
    for (int l = 0; l < L; l++) {
      cin[l] = l == 0 ? DET_CIN : CH; dil[l] = d; hlen[l] = (K - 1) * d + 1; hpos[l] = 0;
      hist[l] = new float[hlen[l] * cin[l]];
      if (!hist[l]) return false;
      memset(hist[l], 0, sizeof(float) * hlen[l] * cin[l]);
      d *= 2;
    }
    return true;
  }
  void reset() { for (int l = 0; l < L; l++) { memset(hist[l], 0, sizeof(float) * hlen[l] * cin[l]); hpos[l] = 0; } }
  const float *W(int l) const { return l == 0 ? &DET_W0[0][0][0] : &DET_W[l - 1][0][0][0]; }
  const float *B(int l) const { return l == 0 ? DET_B0 : DET_B[l - 1]; }
  // x: DET_CIN inputs for one sample. Returns the head output (a logit).
  float step(const float *x) {
    const float *in = x;
    for (int l = 0; l < L; l++) {
      int c = cin[l], n = hlen[l];
      memcpy(hist[l] + hpos[l] * c, in, sizeof(float) * c);
      const float *w = W(l), *b = B(l);
      float *out = (l % 2 == 0) ? buf : buf2;
      for (int o = 0; o < CH; o++) {
        float acc = b[o];
        for (int k = 0; k < K; k++) {
          int idx = hpos[l] - (K - 1 - k) * dil[l]; idx %= n; if (idx < 0) idx += n;
          const float *h = hist[l] + idx * c;
          const float *wk = w + (o * c) * K + k;
          for (int i = 0; i < c; i++) acc += wk[i * K] * h[i];
        }
        out[o] = acc > 0 ? acc : 0;
      }
      hpos[l] = (hpos[l] + 1) % n;
      in = out;
    }
    float y = DET_HB;
    for (int i = 0; i < CH; i++) y += DET_HW[i] * in[i];
    return y;
  }
};

// ---------------------------------------------------------------- event finder (svdetnn.detect / svdet.runs)
// Second-by-second: chance > thr marks the second. Gaps of up to 2 s are bridged. A run of at least
// min_dur seconds is an event; it is reported once 3 s have passed without a marked second,
// moved earlier by 'lag' seconds.
struct EventFinder {
  static const int MERGE = 2;
  long start = -1, lastTrue = -1; float peak = 0;
  // call once per second; returns true and fills (a, b) when an event is complete
  bool second(long sec, float prob, long &a, long &b, float &pk) {
    bool on = prob > ALARM_THR;
    if (on) {
      if (start < 0) { start = sec; peak = 0; }
      lastTrue = sec; if (prob > peak) peak = prob;
      return false;
    }
    if (start >= 0 && sec - lastTrue > MERGE) return close(a, b, pk);
    return false;
  }
  bool finish(long &a, long &b, float &pk) { return start >= 0 ? close(a, b, pk) : false; }
  bool close(long &a, long &b, float &pk) {
    long s = start, e = lastTrue + 1; start = -1; pk = peak;
    if (e - s < ALARM_MIN_DUR) return false;
    a = s - ALARM_LAG; if (a < 0) a = 0;
    b = e - ALARM_LAG; if (b < 0) b = 0;
    return true;
  }
};

// ---------------------------------------------------------------- typing network (NN v2)
// Input rows: chest, belly, (airflow = zeros), event marker; TYP_CH picks the rows the network uses.
// Window 30 s before to 60 s after the event start, each signal divided by its RMS in the 30 s before.
const int PRE = 30, POST = 60, NW = (PRE + POST) * FS;   // 720 samples
struct TypeNet {
  // working memory (about 90 KB)
  float x[TYP_CIN][NW];
  float h1[16][NW / 2];
  float h2[32][NW / 4];
  static float conv_at(const float *in, int len, int t, const float *w, int k) {
    // one input row, 'same' padding: kernel centred on t
    int p = k / 2; float s = 0;
    for (int j = 0; j < k; j++) { int u = t + j - p; if (u >= 0 && u < len) s += w[j] * in[u]; }
    return s;
  }
  // win: TYP_CIN x NW input. Returns the score (score - TYP_CUT > 0 means obstructive).
  float run() {
    // layer 1: conv(TYP_CIN->16, k7, pad3) + ReLU + maxpool 2
    for (int o = 0; o < 16; o++)
      for (int t2 = 0; t2 < NW / 2; t2++) {
        float m = 0;
        for (int q = 0; q < 2; q++) {
          int t = 2 * t2 + q; float s = TYP_B1[o];
          for (int i = 0; i < TYP_CIN; i++) s += conv_at(x[i], NW, t, TYP_W1[o][i], 7);
          if (s < 0) s = 0;
          if (q == 0 || s > m) m = s;
        }
        h1[o][t2] = m;
      }
    // layer 2: conv(16->32, k7, pad3) + ReLU + maxpool 2
    for (int o = 0; o < 32; o++)
      for (int t2 = 0; t2 < NW / 4; t2++) {
        float m = 0;
        for (int q = 0; q < 2; q++) {
          int t = 2 * t2 + q; float s = TYP_B2[o];
          for (int i = 0; i < 16; i++) s += conv_at(h1[i], NW / 2, t, TYP_W2[o][i], 7);
          if (s < 0) s = 0;
          if (q == 0 || s > m) m = s;
        }
        h2[o][t2] = m;
      }
    // layer 3: conv(32->32, k5, pad2) + ReLU + average over time, then the output weights
    float y = TYP_OB;
    for (int o = 0; o < 32; o++) {
      double avg = 0;
      for (int t = 0; t < NW / 4; t++) {
        float s = TYP_B3[o];
        for (int i = 0; i < 32; i++) s += conv_at(h2[i], NW / 4, t, TYP_W3[o][i], 5);
        if (s > 0) avg += s;
      }
      y += TYP_OW[o] * (float)(avg / (NW / 4));
    }
    return y;
  }
};

// Ring of chip-filtered chest and belly signals, long enough to build the typing window.
struct EffortRing {
  static const int SEC = 300, N = SEC * FS;
  float v[2][N]; long count = 0;                   // count = samples written so far
  void push(float chest, float belly) { int i = count % N; v[0][i] = chest; v[1][i] = belly; count++; }
  // value at absolute sample index t (0 if outside what is stored)
  float at(int ch, long t) const { if (t < 0 || t >= count || t < count - N) return 0; return v[ch][t % N]; }
};

// svnn.window for one event starting at second t0 and lasting dur seconds, then the rows TYP_CH picks.
inline void build_window(const EffortRing &r, long t0, long dur, float out[][NW]) {
  static float rows[4][NW];                          // static: too big for the XIAO's loop stack
  long a = (t0 - PRE) * FS;
  for (int k = 0; k < 2; k++) {
    for (int j = 0; j < NW; j++) rows[k][j] = r.at(k, a + j);
    double s = 0; for (int j = 0; j < PRE * FS; j++) s += (double)rows[k][j] * rows[k][j];
    float sc = (float)sqrt(s / (PRE * FS));
    for (int j = 0; j < NW; j++) {
      float v = sc > 0 ? rows[k][j] / sc : 0;
      rows[k][j] = v > 20 ? 20 : (v < -20 ? -20 : v);
    }
  }
  for (int j = 0; j < NW; j++) rows[2][j] = 0;            // airflow row (not used by the effort-only network)
  long m1 = PRE * FS + dur * FS; if (m1 > NW) m1 = NW;
  for (int j = 0; j < NW; j++) rows[3][j] = (j >= PRE * FS && j < m1) ? 1.0f : 0.0f;
  for (int c = 0; c < TYP_CIN; c++) memcpy(out[c], rows[TYP_CH[c]], sizeof(float) * NW);
}

// ---------------------------------------------------------------- the whole pipeline
// Feed one 8 Hz sample of the three raw signals (thermistor, chest, belly). Once per second it
// updates the chance of apnea; when an event is complete it is queued, and typed once 60 s of
// signal after its start are available.
struct Event { long start, end; float peak; float score; bool obstructive; bool typed; };

struct Pipeline {
  Bandpass bp[3];
  ChipFilter cf[2];
  Normaliser nm[3];
  DetNet net;
  EventFinder ef;
  EffortRing ring;
  TypeNet *tn = nullptr;
  float secbuf[3][FS]; int nsec = 0;
  long sample = 0, sec = 0;
  float prob = 0;                                  // chance of apnea in the last full second
  static const int QN = 8; Event q[QN]; int qn = 0;  // events waiting to be typed
  bool begin(TypeNet *t) { tn = t; return net.init(); }
  // start a new recording from a clean state
  void restart() {
    for (int c = 0; c < 3; c++) { bp[c] = Bandpass(); nm[c] = Normaliser(); }
    for (int c = 0; c < 2; c++) cf[c] = ChipFilter();
    net.reset(); ef = EventFinder(); ring.count = 0;
    nsec = 0; sample = 0; sec = 0; prob = 0; qn = 0;
  }
  // returns true when 'done' holds a finished, typed event
  bool feed(float therm, float chest, float belly, bool &newSecond, Event &done) {
    newSecond = false;
    float raw[3] = {therm, chest, belly};
    for (int c = 0; c < 3; c++) secbuf[c][nsec] = (float)bp[c].step(raw[c]);
    ring.push((float)cf[0].step(chest), (float)cf[1].step(belly));
    sample++; nsec++;
    if (nsec == FS) {
      nsec = 0;
      float in[3][FS];
      for (int c = 0; c < 3; c++) nm[c].second(secbuf[c], in[c]);
      float y = 0;
      for (int k = 0; k < FS; k++) { float x[DET_CIN]; for (int c = 0; c < DET_CIN; c++) x[c] = in[DET_CH[c]][k]; y = net.step(x); }
      prob = 1.0f / (1.0f + expf(-y));
      newSecond = true;
      long a, b; float pk;
      if (ef.second(sec, prob, a, b, pk)) enqueue(a, b, pk);
      sec++;
    }
    return typeReady(false, done);
  }
  void enqueue(long a, long b, float pk) {
    if (qn >= QN) { for (int i = 1; i < QN; i++) q[i - 1] = q[i]; qn--; }   // queue full: drop the oldest
    q[qn++] = Event{a, b, pk, 0, false, false};
  }
  // type the oldest waiting event once its window is complete (or at the end, with zeros after the end)
  bool typeReady(bool force, Event &done) {
    if (qn == 0) return false;
    Event &e = q[0];
    if (!force && sample < (e.start + POST) * FS) return false;
    build_window(ring, e.start, e.end - e.start, tn->x);
    e.score = tn->run(); e.obstructive = e.score - TYP_CUT > 0; e.typed = true;
    done = e;
    for (int i = 1; i < qn; i++) q[i - 1] = q[i];
    qn--;
    return true;
  }
  // end of recording: close any open event; call repeatedly until it returns false
  bool finish(Event &done) {
    long a, b; float pk;
    if (ef.finish(a, b, pk)) enqueue(a, b, pk);
    return typeReady(true, done);
  }
};

}  // namespace sv
