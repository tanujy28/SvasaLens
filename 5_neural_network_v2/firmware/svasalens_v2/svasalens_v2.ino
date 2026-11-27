// SvasaLens V2 overnight recorder - Seeed XIAO ESP32S3 (plain, no camera board)
//
// One button press starts a new patient's recording (a new numbered file on the SD card).
// The device then:
//   - reads the chest and belly motion sensors and the nose thermistor 8 times a second
//   - finds apneas with the two-hospital detection network
//   - tells each apnea's type (obstructive / central) with the NN v2 typing network
//   - saves every reading to DEVx_Sxxx.csv and every apnea to DEVx_Sxxx_events.csv
// Holding the button for 3 seconds stops and saves the recording.
//
// For a bench test without a button or SD card, open the Serial Monitor (115200) and type  s  + Enter
// to start or stop. Without an SD card the readings are printed on the Serial Monitor only.
//
// DAYTIME TEST (no laptop): press the button TWICE quickly. The device runs the 10-minute test by itself:
//   30 s calibration, 2 min normal breathing, then 6 breath-holds of 10 s with 50 s rest after each
//   (3 relaxed holds and 3 tries, mixed order). A piezo beeper (on the LED pin) gives the cues:
//     1 long low beep   = get ready: RELAXED HOLD in 5 s      2 short high beeps = get ready: TRY in 5 s
//     1 short very high beep = start now                     1 long low beep after the hold = breathe normally
//   Press the button once during a hold (or up to 20 s after) if it was not done properly.
//   At the end it scores itself and saves DEVx_Txxx_result.csv. The LED then blinks the number of holds it
//   found with the right type (0-6).
// Commands from a laptop (optional, not needed for the test):
//   h = relaxed hold starts, t = blocked-breathing try starts, e = that cue ends  (saved in the 'cue' column)
//   v = print every 8 Hz reading on the Serial Monitor too (so the laptop keeps a full copy)

#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include "svcore.h"
#include "svtypes.h"

// ----------------------------------------------------------------- settings
const int DEVICE_ID = 1;              // set to 2 on the second device, so the files never mix
const float THERM_SIGN = 1.0f;        // +1: breathing out warms the bead and raises the voltage (thermistor on the 3V3 side)
const uint32_t I2C_HZ = 50000;        // slow I2C so the coiled cords work reliably
const uint16_t CAL_S = 30;            // calibration: patient lies still and breathes normally
const uint16_t STOP_HOLD_MS = 3000;   // hold the button this long to stop
// daytime test timings (fixed before testing)
const uint16_t T_NORMAL_S = 120, T_HOLD_S = 10, T_REST_S = 50, T_LAST_REST_S = 60, T_READY_S = 5;
const uint8_t  T_N = 6;
const uint16_t T_MATCH_AFTER_S = 5;   // a detection counts for a hold if it overlaps the hold or the 5 s after it
const uint16_t T_MARK_S = 20;         // a button press up to 20 s after a hold marks it 'not done properly'

// ----------------------------------------------------------------- pins (see README)
const uint8_t CHEST_SDA = D4, CHEST_SCL = D5;   // chest pod  (Wire)
const uint8_t BELLY_SDA = D6, BELLY_SCL = D7;   // belly pod  (Wire1)
const uint8_t THERM_PIN = A0;                   // thermistor divider (10K), also called D0
const uint8_t LED_PIN = D1;                     // status LED (+ 220 ohm resistor to GND) and piezo beeper (+ 100 ohm) in parallel
const uint8_t BTN_PIN = D2;                     // push button to GND
const uint8_t SD_CS = D3, SD_SCK = D8, SD_MISO = D9, SD_MOSI = D10;

const uint8_t MPU = 0x68;
const uint32_t PERIOD_US = 20833UL;   // sensors read 48 times a second, averaged in 6s -> 8 Hz

// ----------------------------------------------------------------- motion sensor helpers (as in SvasaLens V1)
void writeReg(TwoWire &w, uint8_t reg, uint8_t val) { w.beginTransmission(MPU); w.write(reg); w.write(val); w.endTransmission(); }
uint8_t readReg(TwoWire &w, uint8_t reg) {
  w.beginTransmission(MPU); w.write(reg); w.endTransmission(false);
  w.requestFrom(MPU, (uint8_t)1); return w.available() ? w.read() : 0xFF;
}
bool readAcc(TwoWire &w, int16_t *v) {
  w.beginTransmission(MPU); w.write(0x3B);
  if (w.endTransmission(false) != 0) return false;
  if (w.requestFrom(MPU, (uint8_t)6) != 6) return false;
  for (uint8_t i = 0; i < 3; i++) { uint8_t hi = w.read(), lo = w.read(); v[i] = (int16_t)(((uint16_t)hi << 8) | lo); }
  return !(v[0] == 0 && v[1] == 0 && v[2] == 0);
}
bool wakeMPU(TwoWire &w) {
  for (uint8_t t = 0; t < 5; t++) { writeReg(w, 0x6B, 0x00); delay(50); if (readReg(w, 0x6B) == 0x00) return true; }
  return false;
}
uint8_t setupMPU(TwoWire &w) {
  uint8_t who = readReg(w, 0x75);
  if (who == 0x00 || who == 0xFF) return 0xFF;
  if (!wakeMPU(w)) return 0xFE;
  writeReg(w, 0x1A, 0x04);            // ~20 Hz low-pass inside the sensor
  writeReg(w, 0x1C, 0x00);            // +-2 g
  if (who != 0x68) writeReg(w, 0x1D, 0x04);
  return who;
}

// breathing direction: the direction (across gravity) in which the pod moves most during calibration
static Axis ax[2];                  // Axis is defined in svtypes.h (the Arduino IDE needs it there)
static void cal_reset(Axis &a) { memset(&a, 0, sizeof(a)); }
static void cal_add(Axis &a, const float *v) {
  for (uint8_t i = 0; i < 3; i++) a.s[i] += v[i];
  a.c[0] += v[0]*v[0]; a.c[1] += v[0]*v[1]; a.c[2] += v[0]*v[2]; a.c[3] += v[1]*v[1]; a.c[4] += v[1]*v[2]; a.c[5] += v[2]*v[2];
}
static void cal_finish(Axis &a, uint16_t n) {
  float m[3], g[3]; for (uint8_t i = 0; i < 3; i++) m[i] = a.s[i] / n;
  float C[3][3] = {{a.c[0]/n - m[0]*m[0], a.c[1]/n - m[0]*m[1], a.c[2]/n - m[0]*m[2]},
                   {0, a.c[3]/n - m[1]*m[1], a.c[4]/n - m[1]*m[2]}, {0, 0, a.c[5]/n - m[2]*m[2]}};
  C[1][0] = C[0][1]; C[2][0] = C[0][2]; C[2][1] = C[1][2];
  float gn = sqrtf(m[0]*m[0] + m[1]*m[1] + m[2]*m[2]);
  for (uint8_t i = 0; i < 3; i++) g[i] = gn > 0 ? m[i] / gn : 0;
  float u[3] = {1, 0.3f, 0.1f};
  for (uint8_t it = 0; it < 40; it++) {
    float d = u[0]*g[0] + u[1]*g[1] + u[2]*g[2];
    for (uint8_t i = 0; i < 3; i++) u[i] -= d * g[i];
    float w[3]; for (uint8_t i = 0; i < 3; i++) w[i] = C[i][0]*u[0] + C[i][1]*u[1] + C[i][2]*u[2];
    float nw = sqrtf(w[0]*w[0] + w[1]*w[1] + w[2]*w[2]); if (nw <= 0) break;
    for (uint8_t i = 0; i < 3; i++) u[i] = w[i] / nw;
  }
  float d = u[0]*g[0] + u[1]*g[1] + u[2]*g[2];
  for (uint8_t i = 0; i < 3; i++) u[i] -= d * g[i];
  float nu = sqrtf(u[0]*u[0] + u[1]*u[1] + u[2]*u[2]);
  for (uint8_t i = 0; i < 3; i++) a.u[i] = nu > 0 ? u[i] / nu : 0;
}

// ----------------------------------------------------------------- state
enum State { IDLE, CALIB, REC, FAULT };
static State state = IDLE;
static sv::Pipeline P;
static sv::TypeNet TN;
static bool sdOk = false, logToSD = false;
static File fData, fEv;
static char baseName[24];
static int session = 0;
static uint32_t nextUs = 0, calN = 0, lastFlush = 0, recStartMs = 0;
static float acc[2][3]; static uint32_t therm = 0; static uint8_t nacc = 0;
static int16_t last[2][3];
static uint16_t errs = 0, nEv = 0, nObs = 0, nCen = 0;
static uint8_t errFlags = 0;
static bool chestOk = false, bellyOk = false;
static uint8_t cue = 0;            // 0 none, 1 relaxed hold, 2 try (daytime test only)
static bool verbose = false;
static bool testMode = false, aborted = false;

// daytime test state
struct Cue { uint8_t kind; float start, end; bool valid, done; };   // kind 1 = relaxed hold, 2 = try
static Cue tc[T_N];
static uint8_t tOrder[T_N];
static struct { long a, b; bool obs; } tev[48]; static uint8_t ntev = 0;
static long tEndSec = 0;

// ----------------------------------------------------------------- LED
// The LED and the piezo share one pin. 'On' is a 25 kHz wave: the LED glows, the piezo stays silent
// (too high to hear). A beep is an audible tone on the same pin (the LED glows during it).
static uint8_t ledMode = 0;                       // 0 off, 1 on (silent), 2 beeping
static void led(bool on) {
  if (ledMode == 2) return;
  if (on && ledMode != 1) { tone(LED_PIN, 25000); ledMode = 1; }
  else if (!on && ledMode != 0) { noTone(LED_PIN); pinMode(LED_PIN, OUTPUT); digitalWrite(LED_PIN, LOW); ledMode = 0; }
}
static void blinks(int n, int onMs, int offMs) { for (int i = 0; i < n; i++) { led(1); delay(onMs); led(0); delay(offMs); } }

// beep queue: played in the background by beepTick()
struct Note { uint16_t f, on, off; };
static Note notes[12]; static uint8_t nNotes = 0, iNote = 0; static uint32_t noteT = 0; static bool noteOn = false;
static void beep(uint16_t f, uint16_t on, uint16_t off) { if (nNotes < 12) notes[nNotes++] = {f, on, off}; }
static void beepTick() {
  if (iNote >= nNotes) { if (nNotes) { nNotes = iNote = 0; } return; }
  uint32_t t = millis();
  Note &n = notes[iNote];
  if (!noteOn && noteT == 0) {
    if (ledMode == 1) noTone(LED_PIN);
    tone(LED_PIN, n.f); ledMode = 2; noteOn = true; noteT = t;
  } else if (noteOn && t - noteT >= n.on) {
    noTone(LED_PIN); pinMode(LED_PIN, OUTPUT); digitalWrite(LED_PIN, LOW); ledMode = 0; noteOn = false; noteT = t;
  } else if (!noteOn && t - noteT >= n.off) {
    iNote++; noteT = 0;
  }
}
static bool beeping() { return iNote < nNotes; }
// the cue sounds
static void sndReadyHold() { beep(2500, 800, 120); }
static void sndReadyTry()  { beep(3500, 150, 150); beep(3500, 150, 120); }
static void sndGo()        { beep(4000, 300, 120); }
static void sndBreathe()   { beep(2000, 700, 120); }
static void sndMarked()    { beep(1500, 100, 100); beep(1500, 100, 120); }
static void showNumber(int n) {          // long blinks = tens, short blinks = units (0 units = one very short)
  blinks(n / 10, 700, 400); delay(400);
  if (n % 10) blinks(n % 10, 200, 300); else blinks(1, 60, 300);
}
static void ledTick() {                   // background pattern, called every loop
  if (beeping()) return;
  uint32_t t = millis();
  switch (state) {
    case IDLE:  led((t % 2000) < 80); break;                           // short flash every 2 s: ready
    case CALIB: led(true); break;                                      // steady: calibrating, keep still
    case REC:   led(testMode ? (cue != 0) : ((t % 10000) < 40)); break; // night: tiny flash every 10 s. test: on during holds
    case FAULT: led((t % 300) < 150); break;                           // fast blinking: problem
  }
}

// ----------------------------------------------------------------- self-test (network weights)
static bool selfTest() {
#ifdef SELFTEST
  float md = 0;
  for (int s = 0; s < ST_SEC; s++) {
    float y = 0; for (int k = 0; k < 8; k++) y = P.net.step(ST_DET_IN[s * 8 + k]);
    float d = fabsf(y - ST_DET_LOGIT[s]); if (d > md) md = d;
  }
  P.net.reset();
  memcpy(TN.x, ST_TYP_IN, sizeof(ST_TYP_IN));
  uint32_t t0 = millis(); float sc = TN.run(); uint32_t dt = millis() - t0;
  bool ok = md < 1e-3f && fabsf(sc - ST_TYP_SCORE) < 1e-3f;
  Serial.printf("# SELF-TEST %s  (detection diff %.1e, typing %.5f vs %.5f, typing took %lu ms)\n",
                ok ? "PASS" : "FAIL", md, sc, ST_TYP_SCORE, (unsigned long)dt);
  return ok;
#else
  Serial.println("# no self-test in svweights.h"); return true;
#endif
}

// ----------------------------------------------------------------- SD files
static bool openSession(char kind) {
  logToSD = false;
  if (!sdOk) sdOk = SD.begin(SD_CS, SPI, 4000000);
  if (!sdOk) { Serial.println("# NO SD CARD - readings go to the Serial Monitor only"); return false; }
  for (session = 1; session < 1000; session++) {
    snprintf(baseName, sizeof(baseName), "/DEV%d_%c%03d", DEVICE_ID, kind, session);
    char f[48]; snprintf(f, sizeof(f), "%s.csv", baseName);
    if (!SD.exists(f)) break;
  }
  char f[48];
  snprintf(f, sizeof(f), "%s.csv", baseName); fData = SD.open(f, FILE_WRITE);
  snprintf(f, sizeof(f), "%s_events.csv", baseName); fEv = SD.open(f, FILE_WRITE);
  if (!fData || !fEv) { Serial.println("# could not create files on the SD card"); sdOk = false; return false; }
  fData.printf("# SvasaLens V2 device %d %s %d. 8 readings per second. chest/belly = breathing movement (milli-g along the breathing direction)\n", DEVICE_ID, kind == 'T' ? "daytime test" : "session", session);
  fData.println("t_s,therm_mV,chest,belly,chest_x,chest_y,chest_z,belly_x,belly_y,belly_z,apnea_chance,err,cue");
  fEv.printf("# SvasaLens V2 device %d %s %d. Times are seconds from the end of calibration.\n", DEVICE_ID, kind == 'T' ? "daytime test" : "session", session);
  fEv.println("event,start_s,end_s,length_s,type,type_score,peak_chance,start_hh:mm:ss");
  fData.flush(); fEv.flush();
  logToSD = true;
  Serial.printf("# recording to %s.csv\n", baseName);
  return true;
}

static void writeEvent(const sv::Event &e) {
  nEv++; if (e.obstructive) nObs++; else nCen++;
  if (ntev < 48) { tev[ntev].a = e.start; tev[ntev].b = e.end; tev[ntev].obs = e.obstructive; ntev++; }
  long s = e.start;
  char line[120];
  snprintf(line, sizeof(line), "%u,%ld,%ld,%ld,%s,%.4f,%.3f,%02ld:%02ld:%02ld", nEv, e.start, e.end, e.end - e.start,
           e.obstructive ? "OBSTRUCTIVE" : "CENTRAL", e.score - TYP_CUT, e.peak, s / 3600, (s / 60) % 60, s % 60);
  Serial.printf("EVENT,%s\n", line);
  if (logToSD) { fEv.println(line); fEv.flush(); }
}

// ----------------------------------------------------------------- start / stop
// mixed order of 3 relaxed holds (1) and 3 tries (2), never 3 of a kind in a row; different for every test number
static void makeOrder(uint32_t seed) {
  uint32_t r = seed * 2654435761u + 12345u;
  for (;;) {
    uint8_t o[T_N] = {1, 1, 1, 2, 2, 2};
    for (int i = T_N - 1; i > 0; i--) { r = r * 1664525u + 1013904223u; int j = (r >> 8) % (i + 1); uint8_t x = o[i]; o[i] = o[j]; o[j] = x; }
    bool ok = true; for (int i = 0; i + 2 < T_N; i++) if (o[i] == o[i + 1] && o[i + 1] == o[i + 2]) ok = false;
    if (ok) { memcpy(tOrder, o, T_N); return; }
  }
}

static void startSession() {
  bool sd = openSession(testMode ? 'T' : 'S');
  chestOk = setupMPU(Wire) < 0xFE; bellyOk = setupMPU(Wire1) < 0xFE;
  Serial.printf("# chest sensor %s, belly sensor %s\n", chestOk ? "OK" : "MISSING", bellyOk ? "OK" : "MISSING");
  if (!chestOk || !bellyOk) { state = FAULT; if (logToSD) { fData.close(); fEv.close(); } return; }
  if (sd) showNumber(session);
  cal_reset(ax[0]); cal_reset(ax[1]); calN = 0;
  nEv = nObs = nCen = errs = 0; errFlags = 0;
  P.restart();
  nacc = 0; therm = 0; memset(acc, 0, sizeof(acc));
  ntev = 0; aborted = false; cue = 0;
  if (testMode) {
    makeOrder(DEVICE_ID * 1000 + session);
    for (int k = 0; k < T_N; k++) tc[k] = {tOrder[k], 0, 0, true, false};
    tEndSec = T_NORMAL_S + (T_N - 1) * (T_HOLD_S + T_REST_S) + T_HOLD_S + T_LAST_REST_S;
    Serial.print("# DAYTIME TEST, order:"); for (int k = 0; k < T_N; k++) Serial.print(tOrder[k] == 1 ? " HOLD" : " TRY"); Serial.println();
  }
  beep(3000, 150, 120);
  state = CALIB; nextUs = micros();
  Serial.println("# calibrating 30 s: patient lies still and breathes normally");
}

// score the daytime test with the same rule as the laptop version, and save DEVx_Txxx_result.csv
static int scoreTest() {
  int match[T_N]; bool used[48] = {false};
  for (int k = 0; k < T_N; k++) match[k] = -1;
  for (;;) {                                   // greedy: largest overlap first, one detection per hold
    float best = 0; int bk = -1, bj = -1;
    for (int k = 0; k < T_N; k++) {
      if (!tc[k].done || match[k] >= 0) continue;
      for (int j = 0; j < ntev; j++) {
        if (used[j]) continue;
        float o = fminf(tc[k].end + T_MATCH_AFTER_S, (float)tev[j].b) - fmaxf(tc[k].start, (float)tev[j].a);
        if (o >= 1 && o > best) { best = o; bk = k; bj = j; }
      }
    }
    if (bk < 0) break;
    match[bk] = bj; used[bj] = true;
  }
  int holds = 0, found = 0, right = 0, fa = 0; float heldS = 0;
  char f[48]; snprintf(f, sizeof(f), "%s_result.csv", baseName);
  File r; if (logToSD) r = SD.open(f, FILE_WRITE);
  char line[160];
  snprintf(line, sizeof(line), "# SvasaLens daytime test, device %d test %d%s", DEVICE_ID, session, aborted ? ", STOPPED EARLY" : "");
  Serial.println(line); if (r) r.println(line);
  const char *hdr = "hold,kind,start_s,end_s,done_properly,found,device_type,type_right";
  Serial.println(hdr); if (r) r.println(hdr);
  for (int k = 0; k < T_N; k++) {
    if (!tc[k].done) continue;
    bool fnd = match[k] >= 0; bool obs = fnd && tev[match[k]].obs;
    bool ok = fnd && (obs == (tc[k].kind == 2));
    if (tc[k].valid) { holds++; found += fnd; right += ok; }
    heldS += tc[k].end - tc[k].start + T_MATCH_AFTER_S;
    snprintf(line, sizeof(line), "%d,%s,%.2f,%.2f,%d,%d,%s,%d", k + 1, tc[k].kind == 1 ? "HOLD" : "TRY", tc[k].start, tc[k].end,
             tc[k].valid, fnd, fnd ? (obs ? "OBSTRUCTIVE" : "CENTRAL") : "", ok);
    Serial.println(line); if (r) r.println(line);
  }
  for (int j = 0; j < ntev; j++) if (!used[j]) fa++;
  float normalMin = fmaxf(0, P.sample / 8.0f - heldS) / 60.0f;
  snprintf(line, sizeof(line), "# found %d of %d, right type %d of %d, false alarms %d in %.1f min of normal breathing",
           found, holds, right, holds, fa, normalMin);
  Serial.println(line); if (r) r.println(line);
  snprintf(line, sizeof(line), "SUMMARY,%d,%d,%d,%d,%d,%d,%.2f,%d", DEVICE_ID, session, holds, found, right, fa, normalMin, aborted);
  Serial.println(line); if (r) { r.println(line); r.close(); }
  return right;
}

static void stopSession() {
  sv::Event e;
  while (P.finish(e)) writeEvent(e);
  int right = testMode ? scoreTest() : -1;
  float h = P.sample / 8.0f / 3600.0f;
  char line[160];
  snprintf(line, sizeof(line), "# recorded %.2f h, %u apneas found (%u obstructive, %u central), %.1f per hour, %u sensor read errors",
           h, nEv, nObs, nCen, h > 0 ? nEv / h : 0, errs);
  Serial.println(line);
  if (logToSD) { fEv.println(line); fData.close(); fEv.close(); }
  logToSD = false;
  state = IDLE;
  cue = 0;
  if (testMode) {                              // three rising beeps, then the LED blinks the score
    beep(2000, 150, 120); beep(3000, 150, 120); beep(4000, 300, 120);
    while (beeping()) { beepTick(); delay(5); }
    delay(800); blinks(right, 400, 400);
    if (right == 0) blinks(1, 60, 300);
    testMode = false;
  } else blinks(3, 600, 300);
  Serial.println("# stopped and saved. Press the button for the next patient.");
}

// ----------------------------------------------------------------- button
static void buttonStart(bool test) {
  testMode = test;
  startSession();
  if (state == CALIB && !logToSD) { Serial.println("# button start needs an SD card - stopped"); state = FAULT; testMode = false; }
}
static void button() {
  static bool was = false; static uint32_t t0 = 0, lastTap = 0; static bool fired = false, waiting = false;
  bool now = digitalRead(BTN_PIN) == LOW;
  uint32_t t = millis();
  if (now && !was) { t0 = t; fired = false; }
  if (now && !fired && (state == REC || state == CALIB) && t - t0 >= STOP_HOLD_MS) { fired = true; aborted = testMode; stopSession(); }
  if (!now && was && !fired && t - t0 > 50 && t - t0 < 1000) {
    if (state == IDLE || state == FAULT) {
      if (waiting && t - lastTap < 700) { waiting = false; buttonStart(true); }   // two presses: daytime test
      else { waiting = true; lastTap = t; }
    } else if (state == REC && testMode) {     // press during or just after a hold: that hold was not done properly
      float now_s = P.sample / 8.0f;
      for (int k = T_N - 1; k >= 0; k--)
        if (tc[k].start > 0 && now_s >= tc[k].start && (!tc[k].done || now_s <= tc[k].end + T_MARK_S)) {
          tc[k].valid = false; sndMarked(); Serial.printf("# hold %d marked as not done properly\n", k + 1); break;
        }
    } else if (state == REC) { blinks(2, 100, 150); }    // short press while recording: two blinks = still recording
  }
  if (waiting && !now && t - lastTap >= 700) { waiting = false; buttonStart(false); }   // one press: overnight recording
  was = now;
}

// daytime test: called once per 8 Hz sample while recording
static void testTick() {
  if (!testMode) return;
  long n = P.sample;                                          // samples since the end of calibration
  for (int k = 0; k < T_N; k++) {
    long st = (long)(T_NORMAL_S + k * (T_HOLD_S + T_REST_S)) * 8;
    if (n == st - (long)T_READY_S * 8) { if (tc[k].kind == 1) sndReadyHold(); else sndReadyTry(); }
    if (n == st) { cue = tc[k].kind; tc[k].start = n / 8.0f; sndGo(); Serial.printf("CUE,%.3f,%s\n", n / 8.0f, cue == 1 ? "HOLD" : "TRY"); }
    if (n == st + (long)T_HOLD_S * 8) { cue = 0; tc[k].end = n / 8.0f; tc[k].done = true; sndBreathe(); Serial.printf("CUE,%.3f,END\n", n / 8.0f); }
  }
  if (n >= tEndSec * 8) stopSession();
}

// ----------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  uint32_t t0 = millis(); while (!Serial && millis() - t0 < 2000) {}
  setCpuFrequencyMhz(160);
  pinMode(LED_PIN, OUTPUT); pinMode(BTN_PIN, INPUT_PULLUP);
  analogReadResolution(12);
  Wire.begin(CHEST_SDA, CHEST_SCL, I2C_HZ);  Wire.setTimeOut(25);
  Wire1.begin(BELLY_SDA, BELLY_SCL, I2C_HZ); Wire1.setTimeOut(25);
  SPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  Serial.printf("\n# SvasaLens V2 device %d\n", DEVICE_ID);
  if (!P.begin(&TN)) { Serial.println("# out of memory"); state = FAULT; return; }
  if (!selfTest()) { state = FAULT; return; }
  sdOk = SD.begin(SD_CS, SPI, 4000000);
  uint8_t a = setupMPU(Wire), b = setupMPU(Wire1);
  Serial.printf("# SD card %s, chest sensor 0x%02X, belly sensor 0x%02X (0x68/0x70/0x71 = OK)\n", sdOk ? "OK" : "MISSING", a, b);
  Serial.println("# ready: press the button (or type s) to start");
  state = IDLE;
}

void loop() {
  beepTick();
  ledTick();
  button();
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 's' || c == 'S') { if (state == REC || state == CALIB) { aborted = testMode; stopSession(); } else { cue = 0; testMode = false; startSession(); } }
    else if (c == 'd' && (state == IDLE || state == FAULT)) { testMode = true; startSession(); }   // daytime test from the Serial Monitor
    else if (c == 'v') verbose = true;
    else if (c == 'q') verbose = false;
    else if ((c == 'h' || c == 't' || c == 'e') && state == REC) {
      cue = c == 'h' ? 1 : (c == 't' ? 2 : 0);
      Serial.printf("CUE,%.3f,%s\n", P.sample / 8.0f, c == 'h' ? "HOLD" : (c == 't' ? "TRY" : "END"));
    }
  }
  if (state != CALIB && state != REC) return;
  int32_t late = (int32_t)(micros() - nextUs);
  if (late < 0) return;
  if (late > 1000000) { nextUs = micros(); errs++; errFlags |= 4; }   // fell more than 1 s behind: resync
  nextUs += PERIOD_US;

  // one read of both sensors and the thermistor (48 times a second)
  int16_t va[3], vb[3];
  if (readAcc(Wire, va)) memcpy(last[0], va, sizeof(va)); else { errs++; errFlags |= 1; if (errs % 50 == 0) wakeMPU(Wire); }
  if (readAcc(Wire1, vb)) memcpy(last[1], vb, sizeof(vb)); else { errs++; errFlags |= 2; if (errs % 50 == 0) wakeMPU(Wire1); }
  for (uint8_t i = 0; i < 3; i++) { acc[0][i] += last[0][i] / 16384.0f; acc[1][i] += last[1][i] / 16384.0f; }
  therm += analogReadMilliVolts(THERM_PIN);
  if (++nacc < 6) return;

  // one 8 Hz sample
  float v[2][3];
  for (uint8_t s = 0; s < 2; s++) for (uint8_t i = 0; i < 3; i++) { v[s][i] = acc[s][i] / 6.0f; acc[s][i] = 0; }
  float mv = therm / 6.0f; therm = 0; nacc = 0;

  if (state == CALIB) {
    cal_add(ax[0], v[0]); cal_add(ax[1], v[1]);
    if (++calN == CAL_S * 8u) {
      cal_finish(ax[0], calN); cal_finish(ax[1], calN);
      state = REC; recStartMs = millis(); lastFlush = millis(); errFlags = 0;
      beep(3000, 150, 150); beep(3000, 150, 120);
      Serial.println("# recording. Hold the button 3 s (or type s) to stop.");
      Serial.println("# t_s,therm_mV,chest,belly,apnea_chance");
    }
    return;
  }

  float tilt[2];
  for (uint8_t s = 0; s < 2; s++) tilt[s] = 1000.0f * (v[s][0]*ax[s].u[0] + v[s][1]*ax[s].u[1] + v[s][2]*ax[s].u[2]);

  bool newSec; sv::Event e;
  float tsec = P.sample / 8.0f;
  if (P.feed(THERM_SIGN * mv, tilt[0], tilt[1], newSec, e)) writeEvent(e);

  if (logToSD) {
    char line[128]; int n;
    n = snprintf(line, sizeof(line), "%.3f,%.1f,%.2f,%.2f,%d,%d,%d,%d,%d,%d,", tsec, mv, tilt[0], tilt[1],
                 (int)lroundf(v[0][0] * 1000), (int)lroundf(v[0][1] * 1000), (int)lroundf(v[0][2] * 1000),
                 (int)lroundf(v[1][0] * 1000), (int)lroundf(v[1][1] * 1000), (int)lroundf(v[1][2] * 1000));
    if (newSec) n += snprintf(line + n, sizeof(line) - n, "%.3f", P.prob);
    snprintf(line + n, sizeof(line) - n, ",%u,%u", errFlags, cue);
    fData.println(line);
    if (millis() - lastFlush > 10000) { fData.flush(); lastFlush = millis(); }
  }
  if (verbose)
    Serial.printf("R,%.3f,%.1f,%.2f,%.2f,%d,%d,%d,%d,%d,%d,%s%.3f,%u,%u\n", tsec, mv, tilt[0], tilt[1],
                  (int)lroundf(v[0][0] * 1000), (int)lroundf(v[0][1] * 1000), (int)lroundf(v[0][2] * 1000),
                  (int)lroundf(v[1][0] * 1000), (int)lroundf(v[1][1] * 1000), (int)lroundf(v[1][2] * 1000),
                  "", newSec ? P.prob : -1.0f, errFlags, cue);
  else if (newSec) Serial.printf("%.0f,%.1f,%.2f,%.2f,%.3f\n", tsec, mv, tilt[0], tilt[1], P.prob);
  errFlags = 0;
  testTick();
}
