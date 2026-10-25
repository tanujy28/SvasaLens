#ifndef DETECTOR_H
#define DETECTOR_H
#include <math.h>
#include <stdint.h>
#include <string.h>

#define FS 8
#define GUARD_S 20
#define TAU_S 300.0f
#define WARM_S 60
#define SKIP_N 24
#define MIN_N 16
#ifndef RH_MAX
#define RH_MAX 112
#endif
#define SKIP_RH (SKIP_N / 2)
#define LAG_LO 8
#define LAG_HI 32

static const float HP1[5] = {0.9459768533706665f, -1.891953706741333f, 0.9459768533706665f, -1.8890330791473389f, 0.8948743343353271f};
static const float LP1[5] = {0.029954582452774048f, 0.059909164905548096f, 0.029954582452774048f, -1.454243540763855f, 0.574061930179596f};
static const float RULE_C0 = 1.0999081134796143f;
static const float RULE_W_EFFORT = 2.603668451309204f;
static const float RULE_W_RHYTHM = 5.435161113739014f;

typedef struct {
  float z[4];
  float sec;
  float ring[GUARD_S];
  uint8_t ok[GUARD_S];
  float base;
  uint16_t nb;
  float s1, s2, sta;
  float s1a, s2a, staa;
  float buf[RH_MAX];
  uint8_t nbuf;
} Chan;

typedef struct {
  Chan ch[2];
  uint32_t k, since, n;
  uint8_t inev, pos, valid;
  float b0[2];
} Detector;

typedef struct { float effort, rhythm, z; uint32_t n; uint8_t obstructive; } Result;

static float bq(const float *c, float *z, float x) {
  float y = c[0] * x + z[0];
  z[0] = c[1] * x - c[3] * y + z[1];
  z[1] = c[2] * x - c[4] * y;
  return y;
}

static float resid_ms(float s1, float s2, float sta, uint32_t n) {
  float N = (float)n;
  float tm = (N - 1.0f) * 0.5f;
  float stt = N * (N * N - 1.0f) / 12.0f;
  float saa = s2 - s1 * s1 / N;
  float cta = sta - tm * s1;
  float r = saa - cta * cta / stt;
  if (r < 0.0f) r = 0.0f;
  return r / N;
}

static float rhythm_of(float *x, uint8_t n) {
  uint8_t hi = n / 2; if (hi > LAG_HI) hi = LAG_HI;
  if (hi <= LAG_LO) return 0.0f;
  float N = (float)n, tm = (N - 1.0f) * 0.5f;
  float m = 0.0f;
  for (uint8_t i = 0; i < n; i++) m = m + x[i];
  m = m / N;
  float st = 0.0f, stt = 0.0f;
  for (uint8_t i = 0; i < n; i++) { float t = (float)i - tm; st = st + t * (x[i] - m); stt = stt + t * t; }
  float slope = st / stt;
  for (uint8_t i = 0; i < n; i++) x[i] = (x[i] - m) - slope * ((float)i - tm);
  float var = 0.0f;
  for (uint8_t i = 0; i < n; i++) var = var + x[i] * x[i];
  var = var / N;
  if (var <= 0.0f) return 0.0f;
  float best = -9.0f;
  for (uint8_t L = LAG_LO; L <= hi; L++) {
    float acc = 0.0f;
    for (uint8_t i = 0; i + L < n; i++) acc = acc + x[i] * x[i + L];
    float c = (acc / (float)(n - L)) / var;
    if (c > best) best = c;
  }
  return best;
}

static void det_init(Detector *d) {
  memset(d, 0, sizeof(*d));
  d->since = 1000000UL;
}

static uint8_t det_feed(Detector *d, float rib, float abd, uint8_t ev, Result *r) {
  uint8_t out = 0;
  float a[2], in[2] = {rib, abd};
  for (uint8_t c = 0; c < 2; c++) {
    Chan *h = &d->ch[c];
    a[c] = bq(LP1, h->z + 2, bq(HP1, h->z + 0, in[c]));
  }
  if (ev && !d->inev) {
    d->inev = 1; d->n = 0;
    d->b0[0] = d->ch[0].base; d->b0[1] = d->ch[1].base;
    d->valid = d->ch[0].nb >= WARM_S && d->ch[1].nb >= WARM_S;
    for (uint8_t c = 0; c < 2; c++) {
      Chan *h = &d->ch[c];
      h->s1 = 0.0f; h->s2 = 0.0f; h->sta = 0.0f;
      h->s1a = 0.0f; h->s2a = 0.0f; h->staa = 0.0f; h->nbuf = 0;
      memset(h->ok, 0, GUARD_S);
    }
  }
  if (d->inev && ev) {
    for (uint8_t c = 0; c < 2; c++) {
      Chan *h = &d->ch[c];
      float t = (float)d->n;
      h->s1a = h->s1a + a[c]; h->s2a = h->s2a + a[c] * a[c]; h->staa = h->staa + t * a[c];
      if (d->n >= SKIP_N) {
        float u = (float)(d->n - SKIP_N);
        h->s1 = h->s1 + a[c]; h->s2 = h->s2 + a[c] * a[c]; h->sta = h->sta + u * a[c];
      }
      if ((d->n % 2) == 0 && h->nbuf < RH_MAX) h->buf[h->nbuf++] = in[c];
    }
    d->n++;
  }
  if (d->inev && !ev) {
    d->inev = 0; d->since = 0;
    if (d->valid && d->n > 0) {
      uint8_t useskip = d->n >= SKIP_N + MIN_N;
      uint32_t m = useskip ? d->n - SKIP_N : d->n;
      float amp = 0.0f, rh = -9.0f;
      for (uint8_t c = 0; c < 2; c++) {
        Chan *h = &d->ch[c];
        float q = 0.0f;
        float ms = useskip ? resid_ms(h->s1, h->s2, h->sta, m) : resid_ms(h->s1a, h->s2a, h->staa, d->n);
        if (d->b0[c] > 0.0f) q = sqrtf(ms / d->b0[c]);
        if (q > amp) amp = q;
        uint8_t off = (useskip && h->nbuf > SKIP_RH) ? SKIP_RH : 0;
        float t = rhythm_of(h->buf + off, h->nbuf - off);
        if (t > rh) rh = t;
      }
      float e = amp > 0.001f ? amp : 0.001f;
      r->effort = amp; r->rhythm = rh; r->n = d->n;
      r->z = RULE_C0 + RULE_W_EFFORT * logf(e) + RULE_W_RHYTHM * rh;
      r->obstructive = r->z > 0.0f;
      out = 1;
    }
  }
  for (uint8_t c = 0; c < 2; c++) d->ch[c].sec = d->ch[c].sec + a[c] * a[c];
  d->k++;
  if (d->k % FS == 0) {
    uint8_t clean = !d->inev && d->since >= GUARD_S;
    for (uint8_t c = 0; c < 2; c++) {
      Chan *h = &d->ch[c];
      float old = h->ring[d->pos];
      if (h->ok[d->pos]) {
        if (h->nb < WARM_S) {
          h->nb++;
          h->base = h->base + (old - h->base) / (float)h->nb;
        } else {
          if (h->nb < 30000) h->nb++;
          h->base = h->base + (old - h->base) / TAU_S;
        }
      }
      h->ring[d->pos] = h->sec / (float)FS; h->ok[d->pos] = clean;
      h->sec = 0.0f;
    }
    d->pos = (d->pos + 1) % GUARD_S;
    if (!d->inev) d->since++;
  }
  return out;
}
#endif
