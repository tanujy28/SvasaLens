#include <Wire.h>

const uint8_t       MPU       = 0x68;
const unsigned long PERIOD_US = 10000UL;
const float         SETTLE_S  = 10.0;
const float         RECORD_S  = 60.0;

void writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

uint8_t readReg(uint8_t reg) {
  Wire.beginTransmission(MPU);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

int16_t read16() {
  uint8_t hi = Wire.read();
  uint8_t lo = Wire.read();
  return (int16_t)(((uint16_t)hi << 8) | lo);
}

bool readMPU(int16_t *v) {
  Wire.beginTransmission(MPU);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU, (uint8_t)14) != 14) return false;
  v[0] = read16(); v[1] = read16(); v[2] = read16();
  read16();
  v[3] = read16(); v[4] = read16(); v[5] = read16();
  return true;
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(400000);
  writeReg(0x6B, 0x00);
  delay(100);
  writeReg(0x1A, 0x03);
  writeReg(0x1B, 0x00);
  writeReg(0x1C, 0x00);
  delay(200);

  uint8_t who = readReg(0x75);
  Serial.print(F("# WHO_AM_I 0x")); Serial.println(who, HEX);
  Serial.println(F("# still test: 10 s settle, 60 s record, 100 Hz"));
  Serial.println(F("trial,settle,t_ms,cmd_cdeg,ax,ay,az,gx,gy,gz"));

  int16_t v[6];
  unsigned long errors = 0;
  const unsigned long start = micros();
  unsigned long next = start;

  while (true) {
    unsigned long now = micros();
    if ((long)(now - next) < 0) continue;
    next += PERIOD_US;
    float t = (now - start) * 1e-6;
    if (t >= SETTLE_S + RECORD_S) break;
    if (!readMPU(v)) { errors++; continue; }

    Serial.print(0);                         Serial.print(',');
    Serial.print(t < SETTLE_S ? 1 : 0);      Serial.print(',');
    Serial.print((now - start) / 1000UL);    Serial.print(',');
    Serial.print(0);                         Serial.print(',');
    Serial.print(v[0]); Serial.print(',');
    Serial.print(v[1]); Serial.print(',');
    Serial.print(v[2]); Serial.print(',');
    Serial.print(v[3]); Serial.print(',');
    Serial.print(v[4]); Serial.print(',');
    Serial.println(v[5]);
  }
  Serial.print(F("DONE,i2c_errors=")); Serial.println(errors);
}

void loop() {}
