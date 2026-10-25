#include <Wire.h>

const uint8_t       SA = 0x68;
const unsigned long PERIOD_US = 20000UL;
const float         SETTLE_S  = 10.0;
const float         RECORD_S  = 240.0;

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
void printV(int16_t *v) {
  for (uint8_t i = 0; i < 6; i++) { Serial.print(','); Serial.print(v[i]); }
}

void setup() {
  Serial.begin(115200);
  digitalWrite(BSDA, LOW); digitalWrite(BSCL, LOW);
  sdaFree(); sclFree();
  Serial.println(F("# starting..."));
  delay(50);

  pinMode(A4, INPUT); pinMode(A5, INPUT); delay(5);
  bool asda = digitalRead(A4), ascl = digitalRead(A5);
  Serial.print(F("# A wire test: SDA(A4) ")); Serial.print(asda ? F("ok") : F("LOW <-- problem"));
  Serial.print(F("   SCL(A5) "));             Serial.println(ascl ? F("ok") : F("LOW <-- problem"));
  Wire.begin();
  Wire.setClock(400000);
  Wire.setWireTimeout(25000, true);
  uint8_t wa = setupMPU(SA), wb = bSetupMPU();
  delay(200);
  Serial.print(F("# WHO_AM_I A 0x")); Serial.print(wa, HEX);
  Serial.print(F("  B 0x"));          Serial.println(wb, HEX);
  Serial.print(F("# awake: A ")); Serial.print(aAwake ? F("yes") : F("NO"));
  Serial.print(F("  B "));        Serial.println(bAwake ? F("yes") : F("NO"));
  if (wa == 0xFF) Serial.println(F("# sensor A not answering at 0x68"));
  if (wb == 0xFF) Serial.println(F("# sensor B not answering - check its SDA on D2 and SCL on D3"));
  if (wa == 0xFF || wb == 0xFF || !aAwake || !bAwake) { Serial.println(F("DONE,i2c_errors=setup")); return; }
  Serial.println(F("# hold-try test: 50 Hz, 10 s settle, 240 s record"));
  Serial.println(F("trial,settle,t_ms,cmd_cdeg,a_ax,a_ay,a_az,a_gx,a_gy,a_gz,b_ax,b_ay,b_az,b_gx,b_gy,b_gz"));

  int16_t va[6], vb[6];
  unsigned long errors = 0;
  const unsigned long start = micros();
  unsigned long next = start;
  while (true) {
    unsigned long now = micros();
    if ((long)(now - next) < 0) continue;
    next += PERIOD_US;
    float t = (now - start) * 1e-6;
    if (t >= SETTLE_S + RECORD_S) break;
    if (!readMPU(SA, va) || !bReadMPU(vb)) { errors++; continue; }
    if (vb[0] == 0 && vb[1] == 0 && vb[2] == 0) { errors++; bWake(); bWriteReg(0x1A, 0x04); continue; }
    if (va[0] == 0 && va[1] == 0 && va[2] == 0) { errors++; writeReg(SA, 0x6B, 0x00); continue; }
    Serial.print(0);                      Serial.print(',');
    Serial.print(t < SETTLE_S ? 1 : 0);   Serial.print(',');
    Serial.print((now - start) / 1000UL); Serial.print(',');
    Serial.print(0);
    printV(va); printV(vb);
    Serial.println();
  }
  Serial.print(F("DONE,i2c_errors=")); Serial.println(errors);
}

void loop() {}
