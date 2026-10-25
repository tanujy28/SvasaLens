#include "detector.h"

static Detector D;
static uint32_t idx = 0;
static uint8_t blk[32 * 5];

static void printHex32(uint32_t v) {
  for (int8_t s = 28; s >= 0; s -= 4) Serial.print((uint8_t)((v >> s) & 0xF), HEX);
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(250000);
  Serial.setTimeout(2000);
  det_init(&D);
  Serial.println(F("READY SvasaLens replay v2"));
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c == 'S') {
    det_init(&D); idx = 0;
    Serial.println(F("OK"));
  } else if (c == 'B') {
    int n = Serial.read();
    while (n < 0) n = Serial.read();
    if (n > 32) n = 32;
    size_t got = Serial.readBytes(blk, n * 5);
    if (got != (size_t)(n * 5)) { Serial.println(F("ERR short block")); return; }
    for (uint8_t i = 0; i < n; i++) {
      uint8_t *p = blk + 5 * i;
      int16_t rib = (int16_t)(p[0] | (p[1] << 8));
      int16_t abd = (int16_t)(p[2] | (p[3] << 8));
      Result r;
      idx++;
      if (det_feed(&D, (float)rib, (float)abd, p[4], &r)) {
        uint32_t ue, ur; memcpy(&ue, &r.effort, 4); memcpy(&ur, &r.rhythm, 4);
        Serial.print(F("E,")); Serial.print(idx); Serial.print(',');
        printHex32(ue); Serial.print(','); printHex32(ur); Serial.print(',');
        Serial.print(r.n); Serial.print(',');
        Serial.println(r.obstructive ? F("OBSTRUCTIVE") : F("CENTRAL"));
        digitalWrite(LED_BUILTIN, r.obstructive);
      }
    }
    Serial.println(F("K"));
  }
}
