#ifndef SV_LCD_H
#define SV_LCD_H
#include <Wire.h>
static uint8_t lcd_addr = 0;
static void lcd_x(uint8_t b) { Wire.beginTransmission(lcd_addr); Wire.write(b); Wire.endTransmission(); }
static void lcd_nib(uint8_t n, uint8_t rs) {
  uint8_t b = (n << 4) | rs | 0x08;
  lcd_x(b | 0x04); delayMicroseconds(1);
  lcd_x(b & ~0x04); delayMicroseconds(50);
}
static void lcd_byte(uint8_t v, uint8_t rs) { lcd_nib(v >> 4, rs); lcd_nib(v & 0x0F, rs); }
static bool lcd_probe(uint8_t a) { Wire.beginTransmission(a); return Wire.endTransmission() == 0; }
static bool lcd_begin() {
  const uint8_t tryA[] = {0x27, 0x3F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E};
  for (uint8_t i = 0; i < sizeof(tryA); i++) if (lcd_probe(tryA[i])) { lcd_addr = tryA[i]; break; }
  if (!lcd_addr) return false;
  delay(50);
  lcd_nib(0x03, 0); delay(5); lcd_nib(0x03, 0); delay(5); lcd_nib(0x03, 0); delayMicroseconds(150);
  lcd_nib(0x02, 0);
  lcd_byte(0x28, 0); lcd_byte(0x0C, 0); lcd_byte(0x06, 0); lcd_byte(0x01, 0); delay(2);
  return true;
}
static void lcd_line(uint8_t row, const char *s) {
  if (!lcd_addr) return;
  lcd_byte(0x80 | (row ? 0x40 : 0x00), 0);
  uint8_t i = 0;
  for (; i < 16 && s[i]; i++) lcd_byte(s[i], 1);
  for (; i < 16; i++) lcd_byte(' ', 1);
}
#endif
