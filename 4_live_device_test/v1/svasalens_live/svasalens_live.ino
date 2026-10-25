#include <Wire.h>
#define RH_MAX 48
#include "detector.h"
#include "lcd.h"

struct Axis { float s[3], c[6]; float g[3], u[3]; };

const uint8_t  BUZ = 8;
const uint8_t  SA = 0x68;
const uint32_t PERIOD_US = 20833UL;
const uint16_t CAL_STEPS = 30 * 8;
const uint16_t LEARN_S = 90;
const uint8_t  N_EV = 6;
const uint8_t  EV_S = 10, GAP_S = 30;

const uint8_t BSDA = 2, BSCL = 3, SB = 0x68;
inline void bd()      { delayMicroseconds(3); }
inline void sdaLow()  { pinMode(BSDA, OUTPUT); }
inline void sdaFree() { pinMode(BSDA, INPUT); }
inline void sclLow()  { pinMode(BSCL, OUTPUT); }
inline void sclFree() { pinMode(BSCL, INPUT); }
void bStart() { sdaFree(); sclFree(); bd(); sdaLow(); bd(); sclLow(); bd(); }
void bStop()  { sdaLow(); bd(); sclFree(); bd(); sdaFree(); bd(); }
bool bWrite(uint8_t b) {
  for (uint8_t i = 0; i < 8; i++) {
    if (b & 0x80) sdaFree(); else sdaLow();
    b <<= 1; bd(); sclFree(); bd(); sclLow();
  }
  sdaFree(); bd(); sclFree(); bd();
  bool ack = (digitalRead(BSDA) == LOW);
  sclLow(); bd();
  return ack;
}
uint8_t bRead(bool more) {
  uint8_t b = 0; sdaFree();
  for (uint8_t i = 0; i < 8; i++) {
    b <<= 1; bd(); sclFree(); bd();
    if (digitalRead(BSDA)) b |= 1;
    sclLow();
  }
  if (more) sdaLow(); else sdaFree();
  bd(); sclFree(); bd(); sclLow(); sdaFree(); bd();
  return b;
}
void bWriteReg(uint8_t reg, uint8_t val) {
  bStart(); bWrite(SB << 1); bWrite(reg); bWrite(val); bStop();
}
uint8_t bReadReg(uint8_t reg) {
  bStart();
  if (!bWrite(SB << 1)) { bStop(); return 0xFF; }
  bWrite(reg); bStart(); bWrite((SB << 1) | 1);
  uint8_t v = bRead(false); bStop();
  return v;
}
bool bReadMPU(int16_t *v) {
  uint8_t r[14];
  bStart();
  if (!bWrite(SB << 1)) { bStop(); return false; }
  bWrite(0x3B); bStart();
  if (!bWrite((SB << 1) | 1)) { bStop(); return false; }
  for (uint8_t i = 0; i < 14; i++) r[i] = bRead(i < 13);
  bStop();
  int16_t w[7];
  for (uint8_t i = 0; i < 7; i++) w[i] = (int16_t)(((uint16_t)r[2*i] << 8) | r[2*i+1]);
  v[0] = w[0]; v[1] = w[1]; v[2] = w[2]; v[3] = w[4]; v[4] = w[5]; v[5] = w[6];
  return true;
}
void bRecover() {
  sdaFree();
  for (uint8_t i = 0; i < 9; i++) { sclLow(); bd(); sclFree(); bd(); }
  bStop();
}
bool bWake() {
  for (uint8_t tries = 0; tries < 5; tries++) {
    bRecover();
    bWriteReg(0x6B, 0x00); delay(50);
    if (bReadReg(0x6B) == 0x00) return true;
  }
  return false;
}
bool bAwake = false;
uint8_t bSetupMPU() {
  uint8_t who = bReadReg(0x75);
  if (who == 0x00 || who == 0xFF) { bRecover(); who = bReadReg(0x75); }
  if (who == 0x00) return 0xFF;
  if (who == 0xFF) return 0xFF;
  bAwake = bWake();
  bWriteReg(0x1A, 0x04);
  bWriteReg(0x1B, 0x00);
  bWriteReg(0x1C, 0x00);
  if (who != 0x68) bWriteReg(0x1D, 0x04);
  return who;
}

void writeReg(uint8_t dev, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(dev); Wire.write(reg); Wire.write(val); Wire.endTransmission();
}
uint8_t readReg(uint8_t dev, uint8_t reg) {
  Wire.beginTransmission(dev); Wire.write(reg); Wire.endTransmission(false);
  Wire.requestFrom(dev, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}
int16_t read16() {
  uint8_t hi = Wire.read(); uint8_t lo = Wire.read();
  return (int16_t)(((uint16_t)hi << 8) | lo);
}
bool readMPU(uint8_t dev, int16_t *v) {
  Wire.beginTransmission(dev); Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(dev, (uint8_t)14) != 14) return false;
  v[0] = read16(); v[1] = read16(); v[2] = read16(); read16();
  v[3] = read16(); v[4] = read16(); v[5] = read16();
  return true;
}
bool aAwake = false;
uint8_t setupMPU(uint8_t dev) {
  for (uint8_t tries = 0; tries < 5 && !aAwake; tries++) {
    writeReg(dev, 0x6B, 0x00); delay(50);
    aAwake = (readReg(dev, 0x6B) == 0x00);
  }
  writeReg(dev, 0x1A, 0x04);
  writeReg(dev, 0x1B, 0x00);
  writeReg(dev, 0x1C, 0x00);
  uint8_t who = readReg(dev, 0x75);
  if (who != 0x68) writeReg(dev, 0x1D, 0x04);
  return who;
}

static Axis ax[2];
static void cal_add(Axis &a, const float *v) {
  for (uint8_t i = 0; i < 3; i++) a.s[i] += v[i];
  a.c[0] += v[0]*v[0]; a.c[1] += v[0]*v[1]; a.c[2] += v[0]*v[2];
  a.c[3] += v[1]*v[1]; a.c[4] += v[1]*v[2]; a.c[5] += v[2]*v[2];
}
static void cal_finish(Axis &a, uint16_t n) {
  float m[3]; for (uint8_t i = 0; i < 3; i++) m[i] = a.s[i] / n;
  float C[3][3] = {{a.c[0]/n - m[0]*m[0], a.c[1]/n - m[0]*m[1], a.c[2]/n - m[0]*m[2]},
                   {0, a.c[3]/n - m[1]*m[1], a.c[4]/n - m[1]*m[2]},
                   {0, 0, a.c[5]/n - m[2]*m[2]}};
  C[1][0] = C[0][1]; C[2][0] = C[0][2]; C[2][1] = C[1][2];
  float gn = sqrtf(m[0]*m[0] + m[1]*m[1] + m[2]*m[2]);
  for (uint8_t i = 0; i < 3; i++) a.g[i] = m[i] / gn;
  float u[3] = {1, 0.3f, 0.1f};
  for (uint8_t it = 0; it < 40; it++) {
    float d = u[0]*a.g[0] + u[1]*a.g[1] + u[2]*a.g[2];
    for (uint8_t i = 0; i < 3; i++) u[i] -= d * a.g[i];
    float w[3];
    for (uint8_t i = 0; i < 3; i++) w[i] = C[i][0]*u[0] + C[i][1]*u[1] + C[i][2]*u[2];
    float nw = sqrtf(w[0]*w[0] + w[1]*w[1] + w[2]*w[2]);
    if (nw <= 0) break;
    for (uint8_t i = 0; i < 3; i++) u[i] = w[i] / nw;
  }
  float d = u[0]*a.g[0] + u[1]*a.g[1] + u[2]*a.g[2];
  for (uint8_t i = 0; i < 3; i++) u[i] -= d * a.g[i];
  float nu = sqrtf(u[0]*u[0] + u[1]*u[1] + u[2]*u[2]);
  for (uint8_t i = 0; i < 3; i++) a.u[i] = nu > 0 ? u[i] / nu : 0;
}

static Detector D;
static float acc[2][3]; static uint8_t nacc = 0;
static uint32_t step = 0;
static float zd[2][4];
static float pwr[2];
static int8_t evShown = -1; static uint32_t showUntil = 0;
static char l0[17], l1[17];
static uint8_t lcdTick = 0;
static uint16_t errs = 0;
static uint32_t nextUs = 0;
static char evKind(uint8_t i) { return (i % 2 == 0) ? 'H' : 'T'; }

static void beep(uint16_t f, uint16_t ms) { tone(BUZ, f, ms); }
static uint16_t pendF = 0, pendMs = 0; static uint32_t pendAt = 0;
static void beepLater(uint16_t f, uint16_t ms, uint16_t after) { pendF = f; pendMs = ms; pendAt = millis() + after; }

static float disp_bq(const float *c, float *z, float x) {
  float y = c[0]*x + z[0]; z[0] = c[1]*x - c[3]*y + z[1]; z[1] = c[2]*x - c[4]*y; return y;
}
static void bar(char *s, float r) {
  uint8_t n = (uint8_t)(r * 8.0f + 0.5f); if (n > 10) n = 10;
  for (uint8_t i = 0; i < 10; i++) s[i] = i < n ? '#' : ' ';
  s[10] = ' '; dtostrf(r > 9.99f ? 9.99f : r, 5, 2, s + 11); s[16] = 0;
}

void setup() {
  Serial.begin(115200);
  pinMode(BUZ, OUTPUT);
  digitalWrite(BSDA, LOW); digitalWrite(BSCL, LOW); sdaFree(); sclFree();
  Wire.begin(); Wire.setClock(100000); Wire.setWireTimeout(25000, true);
  bool lcdOk = lcd_begin();
  uint8_t wa = setupMPU(SA), wb = bSetupMPU();
  Serial.print(F("# SvasaLens live v1  LCD ")); Serial.print(lcdOk ? F("yes") : F("NO"));
  Serial.print(F("  A 0x")); Serial.print(wa, HEX); Serial.print(F(" awake ")); Serial.print(aAwake);
  Serial.print(F("  B 0x")); Serial.print(wb, HEX); Serial.print(F(" awake ")); Serial.println(bAwake);
  if (wa == 0xFF || wb == 0xFF || !aAwake || !bAwake) {
    strcpy_P(l0, PSTR("SENSOR PROBLEM"));
    if (wa == 0xFF || !aAwake) strcpy_P(l1, PSTR("check sensor A")); else strcpy_P(l1, PSTR("check sensor B"));
    lcd_line(0, l0); lcd_line(1, l1);
    Serial.println(F("DONE,sensor problem"));
    for (;;) { beep(300, 400); delay(1500); }
  }
  strcpy_P(l0, PSTR("SvasaLens")); strcpy_P(l1, PSTR("calibrating 30s"));
  lcd_line(0, l0); lcd_line(1, l1);
  Serial.println(F("# t_ms,tiltA,tiltB,flag,live_ratio"));
  det_init(&D);
  beep(1200, 120);
  nextUs = micros();
}

void loop() {
  if ((int32_t)(micros() - nextUs) < 0) return;
  nextUs += PERIOD_US;

  if (pendF && (int32_t)(millis() - pendAt) >= 0) { beep(pendF, pendMs); pendF = 0; }
  int16_t va[6], vb[6];
  if (!readMPU(SA, va)) { errs++; return; }
  if (!bReadMPU(vb)) { errs++; return; }
  if (vb[0] == 0 && vb[1] == 0 && vb[2] == 0) { errs++; bWake(); return; }
  for (uint8_t i = 0; i < 3; i++) { acc[0][i] += va[i] / 16384.0f; acc[1][i] += vb[i] / 16384.0f; }
  if (++nacc < 6) return;
  float v[2][3];
  for (uint8_t s = 0; s < 2; s++) for (uint8_t i = 0; i < 3; i++) { v[s][i] = acc[s][i] / 6.0f; acc[s][i] = 0; }
  nacc = 0;
  step++;

  if (step <= CAL_STEPS) {
    cal_add(ax[0], v[0]); cal_add(ax[1], v[1]);
    if (step == CAL_STEPS) { cal_finish(ax[0], CAL_STEPS); cal_finish(ax[1], CAL_STEPS); beep(1200, 120); }
    return;
  }

  float tilt[2];
  for (uint8_t s = 0; s < 2; s++)
    tilt[s] = 1000.0f * (v[s][0]*ax[s].u[0] + v[s][1]*ax[s].u[1] + v[s][2]*ax[s].u[2]);

  uint32_t ts = (step - CAL_STEPS);
  uint32_t sec = ts / 8;
  int8_t ev = -1; int8_t soon = -1;
  if (sec >= LEARN_S) {
    uint32_t k = (sec - LEARN_S) / (EV_S + GAP_S), r = (sec - LEARN_S) % (EV_S + GAP_S);
    if (k < N_EV && r < EV_S) ev = k;
    if (k + 1 < N_EV + 1 && r >= EV_S + GAP_S - 3) soon = k + 1;
  } else if (sec >= LEARN_S - 3) soon = 0;
  if (soon >= N_EV) soon = -1;
  uint8_t flag = ev >= 0;

  static int8_t lastSoon = -1, lastEv = -1;
  if (soon != lastSoon && soon >= 0) beep(880, 150);
  if (ev != lastEv) { if (ev >= 0) { beep(1300, 120); beepLater(1300, 120, 200); } else if (lastEv >= 0) beep(500, 350); }
  lastSoon = soon; lastEv = ev;

  Result res;
  if (det_feed(&D, tilt[0], tilt[1], flag, &res)) {
    uint8_t k = (uint8_t)((sec - LEARN_S) / (EV_S + GAP_S));
    Serial.print(F("D,")); Serial.print(k + 1); Serial.print(','); Serial.print(evKind(k) == 'H' ? F("HOLD") : F("TRY"));
    Serial.print(','); Serial.print(res.effort, 3); Serial.print(','); Serial.print(res.rhythm, 3);
    Serial.print(','); Serial.print(res.z, 3); Serial.print(',');
    Serial.println(res.obstructive ? F("OBSTRUCTIVE") : F("CENTRAL"));
    if (res.obstructive) strcpy_P(l0, PSTR("OBSTRUCTIVE")); else strcpy_P(l0, PSTR("CENTRAL"));
    char e5[6], r5[6]; dtostrf(res.effort > 9.99f ? 9.99f : res.effort, 4, 2, e5); dtostrf(res.rhythm < 0 ? 0.0f : res.rhythm, 4, 2, r5);
    snprintf_P(l1, 17, PSTR("eff %s rh %s"), e5, r5);
    lcd_line(0, l0); lcd_line(1, l1);
    showUntil = ts + 5 * 8; evShown = k;
    if (!res.obstructive) beepLater(2000, 600, 450);
  }

  float live = 0;
  for (uint8_t s = 0; s < 2; s++) {
    float y = disp_bq(LP1, zd[s] + 2, disp_bq(HP1, zd[s], tilt[s]));
    pwr[s] += (y*y - pwr[s]) / 24.0f;
    float b = D.ch[s].base;
    float r = b > 0 ? sqrtf(pwr[s] / b) : 0;
    if (r > live) live = r;
  }

  Serial.print(millis()); Serial.print(','); Serial.print(tilt[0], 3); Serial.print(',');
  Serial.print(tilt[1], 3); Serial.print(','); Serial.print(flag); Serial.print(','); Serial.println(live, 3);

  if (ts < showUntil) return;
  if (++lcdTick < 2) return;
  lcdTick = 0;
  static uint8_t row = 0; row ^= 1;
  if (soon >= 0 && ev < 0) {
    strcpy_P(l0, PSTR("GET READY:")); if (evKind(soon) == 'H') strcpy_P(l1, PSTR("HOLD next")); else strcpy_P(l1, PSTR("TRY next"));
  } else if (sec < LEARN_S) {
    strcpy_P(l0, PSTR("LEARNING NORMAL"));
    snprintf_P(l1, 17, PSTR("breathe...  %3us"), (unsigned)(LEARN_S - sec));
  } else if (ev >= 0) {
    strcpy_P(l0, PSTR("BREATHING STOP")); bar(l1, live);
  } else if (sec >= LEARN_S + N_EV * (EV_S + GAP_S)) {
    strcpy_P(l0, PSTR("DONE")); strcpy_P(l1, PSTR("thank you"));
    static bool said = false; if (!said) { Serial.print(F("DONE,errors=")); Serial.println(errs); said = true; }
  } else {
    strcpy_P(l0, PSTR("BREATHING")); bar(l1, live);
  }
  lcd_line(row, row ? l1 : l0);
}
