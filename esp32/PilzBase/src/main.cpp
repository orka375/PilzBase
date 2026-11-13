#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <Adafruit_MAX31865.h>
#include <Adafruit_TCS34725.h>
#include <Adafruit_BME280.h>

// ESP32-S3 N8R8: GPIO35/36/37 belong to octal PSRAM. DO NOT use them for CS.
constexpr int I2C_SDA = 8, I2C_SCL = 9;
constexpr int SPI_SCK_PIN = 38, SPI_MISO_PIN = 40, SPI_MOSI_PIN = 39;
constexpr int PT100_CS1 = 10, PT100_CS2 = 14;
constexpr int MOISTURE_PIN = 5, LED_PIN = 7;
constexpr int BUTTON_PIN = 3;
constexpr bool BUTTON_ACTIVE_LOW = true;
constexpr bool LED_ACTIVE_HIGH = true;
constexpr float RTD_NOMINAL = 100.0f;    // PT100; use 1000.0 for PT1000
constexpr float REF_RESISTOR = 430.0f;   // Adafruit PT100 boards normally 430 ohms
constexpr max31865_numwires_t RTD_WIRES = MAX31865_2WIRE;
constexpr uint32_t REPORT_INTERVAL_MS = 5000;

constexpr uint8_t ADDR_M5 = 0x55, ADDR_TCS = 0x29, ADDR_SCD = 0x62;
constexpr uint8_t ADDR_ENS_A = 0x52, ADDR_ENS_B = 0x53;

Adafruit_MAX31865 rtd1(PT100_CS1, &SPI), rtd2(PT100_CS2, &SPI);
Adafruit_TCS34725 tcs(TCS34725_INTEGRATIONTIME_50MS, TCS34725_GAIN_4X);
Adafruit_BME280 bme;
bool hasM5 = false, hasTCS = false, hasSCD = false, hasBME = false, hasENS = false;
bool hasRtd1 = false, hasRtd2 = false;
uint8_t ensAddress = 0;
uint32_t lastReport = 0;

bool i2cPresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

bool readRegs(uint8_t addr, uint8_t reg, uint8_t *buf, size_t n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if ((size_t)Wire.requestFrom((int)addr, (int)n) != n) return false;
  for (size_t i = 0; i < n; ++i) buf[i] = Wire.read();
  return true;
}

bool writeReg(uint8_t addr, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

uint16_t le16(const uint8_t *b) { return uint16_t(b[0]) | uint16_t(b[1]) << 8; }
uint16_t be16(const uint8_t *b) { return uint16_t(b[0]) << 8 | uint16_t(b[1]); }

// SCD4x protocol: command words and CRC8 per 2 data bytes (Sensirion polynomial 0x31).
bool scdCommand(uint16_t cmd) {
  Wire.beginTransmission(ADDR_SCD);
  Wire.write(cmd >> 8);
  Wire.write(cmd & 0xff);
  return Wire.endTransmission() == 0;
}
uint8_t crc8(const uint8_t *p) {
  uint8_t crc = 0xff;
  for (int i = 0; i < 2; ++i) {
    crc ^= p[i];
    for (int bit = 0; bit < 8; ++bit)
      crc = (crc & 0x80) ? uint8_t((crc << 1) ^ 0x31) : uint8_t(crc << 1);
  }
  return crc;
}
bool scdWords(uint16_t cmd, uint16_t *words, int count) {
  if (!scdCommand(cmd)) return false;
  delay(2);
  const int n = count * 3;
  if (Wire.requestFrom((int)ADDR_SCD, n) != n) return false;
  for (int i = 0; i < count; ++i) {
    uint8_t b[3] = {uint8_t(Wire.read()), uint8_t(Wire.read()), uint8_t(Wire.read())};
    if (crc8(b) != b[2]) return false;
    words[i] = be16(b);
  }
  return true;
}

void scanI2C() {
  Serial.println("\n--- I2C scan ---");
  int found = 0;
  for (uint8_t addr = 0x08; addr <= 0x77; ++addr) {
    if (i2cPresent(addr)) {
      Serial.printf("  Found 0x%02X\n", addr);
      ++found;
    }
  }
  Serial.printf("  Total devices: %d\n", found);
}

void initSensors() {
  hasM5 = i2cPresent(ADDR_M5);
  hasTCS = i2cPresent(ADDR_TCS) && tcs.begin(ADDR_TCS, &Wire);
  hasSCD = i2cPresent(ADDR_SCD);
  uint8_t bmeAddr = i2cPresent(0x76) ? 0x76 : (i2cPresent(0x77) ? 0x77 : 0);
  hasBME = bmeAddr && bme.begin(bmeAddr, &Wire);
  ensAddress = i2cPresent(ADDR_ENS_A) ? ADDR_ENS_A : (i2cPresent(ADDR_ENS_B) ? ADDR_ENS_B : 0);
  if (ensAddress) {
    uint8_t part[2] = {};
    hasENS = readRegs(ensAddress, 0x00, part, 2) && le16(part) == 0x0160;
    if (hasENS) writeReg(ensAddress, 0x10, 0x02); // ENS160 standard operating mode
  }
  if (hasSCD) {
    // A power cycle or previous firmware may have left periodic measurement active.
    scdCommand(0x3F86); // stop periodic measurement
    delay(500);
    hasSCD = scdCommand(0x21B1); // start periodic measurement (new values every ~5s)
  }

  SPI.begin(SPI_SCK_PIN, SPI_MISO_PIN, SPI_MOSI_PIN);
  // begin() indicates communication setup, not confirmed sensor presence.
  hasRtd1 = rtd1.begin(RTD_WIRES);
  hasRtd2 = rtd2.begin(RTD_WIRES);
  if (hasRtd1) rtd1.enable50Hz(true);
  if (hasRtd2) rtd2.enable50Hz(true);

  Serial.printf("M5 4-20mA: %s\n", hasM5 ? "address found" : "NOT FOUND");
  Serial.printf("TCS34725: %s\n", hasTCS ? "OK" : "NOT FOUND / init failed");
  Serial.printf("SCD41: %s\n", hasSCD ? "started, wait >= 5s" : "NOT FOUND / start failed");
  Serial.printf("BME280: %s\n", hasBME ? "OK" : "NOT FOUND / wrong chip ID");
  Serial.printf("ENS160: %s\n", hasENS ? "OK, warming up" : "NOT FOUND / wrong chip ID");
  Serial.printf("MAX31865 #1: %s (not proof of physical presence)\n", hasRtd1 ? "SPI initialized" : "FAILED");
  Serial.printf("MAX31865 #2: %s (not proof of physical presence)\n", hasRtd2 ? "SPI initialized" : "FAILED");
}

void reportMAX(const char *name, Adafruit_MAX31865 &sensor, bool initialized) {
  if (!initialized) { Serial.printf("[FAIL] %s: init failed\n", name); return; }
  sensor.clearFault();
  uint16_t raw = sensor.readRTD(); // 15-bit RTD raw code
  uint8_t fault = sensor.readFault();
  if (fault || raw == 0 || raw >= 0x7fff) {
    Serial.printf("[WARN] %s: raw=%u fault=0x%02X; check SPI, PT100 wiring and jumpers\n", name, raw, fault);
    return;
  }
  float ohms = (raw / 32768.0f) * REF_RESISTOR;
  float temp = sensor.calculateTemperature(raw, RTD_NOMINAL, REF_RESISTOR);
  Serial.printf("[OK]   %s: raw=%u, R=%.2f ohm, T=%.2f C\n", name, raw, ohms, temp);
}

void reportSensors() {
  Serial.printf("\n--- Measurements (uptime %lu s) ---\n", millis()/1000UL);
  if (hasM5) {
    uint8_t d[2];
    if (readRegs(ADDR_M5, 0x20, d, 2))
      Serial.printf("[OK]   M5 AIN 4-20mA: %.2f mA\n", le16(d) / 100.0f);
    else Serial.println("[FAIL] M5 current register read");
  } else Serial.println("[MISS] M5 AIN 4-20mA (0x55)");

  if (hasTCS) {
    uint16_t r,g,b,c;
    tcs.getRawData(&r,&g,&b,&c);
    Serial.printf("[OK]   TCS34725: R=%u G=%u B=%u Clear=%u\n",r,g,b,c);
  } else Serial.println("[MISS] TCS34725 (0x29)");

  if (hasSCD) {
    uint16_t ready;
    if (scdWords(0xE4B8, &ready, 1)) {
      if ((ready & 0x07FF) == 0) Serial.println("[WAIT] SCD41: next sample not ready");
      else {
        uint16_t data[3];
        if (scdWords(0xEC05, data, 3))
          Serial.printf("[OK]   SCD41: CO2=%u ppm, T=%.2f C, RH=%.1f %%\n",
                        data[0], -45.0f + 175.0f*data[1]/65536.0f,
                        100.0f*data[2]/65536.0f);
        else Serial.println("[FAIL] SCD41 measurement/CRC");
      }
    } else Serial.println("[FAIL] SCD41 data-ready/CRC");
  } else Serial.println("[MISS] SCD41 (0x62)");

  if (hasBME) Serial.printf("[OK]   BME280: T=%.2f C, RH=%.1f %%, P=%.1f hPa\n",
                           bme.readTemperature(), bme.readHumidity(), bme.readPressure()/100.0f);
  else Serial.println("[MISS] BME280 (0x76/0x77)");

  if (hasENS) {
    uint8_t d[5]; // status at 0x20, AQI 0x21, TVOC 0x22..23, eCO2 0x24..25
    uint8_t data[6];
    if (readRegs(ensAddress, 0x20, data, sizeof(data))) {
      uint8_t validity = (data[0] >> 2) & 0x03;
      Serial.printf("[%s] ENS160: validity=%u, AQI=%u, TVOC=%u ppb, eCO2=%u ppm\n",
                    validity == 0 ? "OK" : "WAIT", validity, data[1],
                    le16(data + 2), le16(data + 4));
    } else Serial.println("[FAIL] ENS160 read");
  } else Serial.println("[MISS] ENS160 (0x52/0x53)");

  uint16_t adc = analogRead(MOISTURE_PIN);
  uint32_t mv = analogReadMilliVolts(MOISTURE_PIN);
  Serial.printf("[OK]   Grove moisture GPIO%d: ADC=%u/4095, approx=%lu mV (calibrate wet/dry)\n",
                MOISTURE_PIN, adc, (unsigned long)mv);

  reportMAX("MAX31865 #1", rtd1, hasRtd1);
  reportMAX("MAX31865 #2", rtd2, hasRtd2);

  if (BUTTON_PIN >= 0)
    Serial.printf("[OK]   Button GPIO%d: %s\n", BUTTON_PIN,
                  (digitalRead(BUTTON_PIN) == (BUTTON_ACTIVE_LOW ? LOW : HIGH)) ? "PRESSED" : "released");
  else Serial.println("[SKIP] Button: set BUTTON_PIN in main.cpp");
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\nESP32-S3 multi-sensor diagnostic (Arduino + PlatformIO)");
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LED_ACTIVE_HIGH ? LOW : HIGH);
  pinMode(BUTTON_PIN, INPUT_PULLDOWN);
  analogReadResolution(12);
  analogSetPinAttenuation(MOISTURE_PIN, ADC_11db);
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);
  Wire.setTimeOut(100);
  scanI2C();
  initSensors();
  lastReport = millis() - REPORT_INTERVAL_MS;
}

void loop() {
  // Toggle external LED once per second; if LED is RGB/addressable, use its own driver instead.
  static uint32_t lastBlink = 0;
  static bool ledOn = false;
  if (millis() - lastBlink >= 1000) {
    lastBlink = millis();
    ledOn = !ledOn;
    digitalWrite(LED_PIN, (ledOn == LED_ACTIVE_HIGH) ? HIGH : LOW);
  }
  if (millis() - lastReport >= REPORT_INTERVAL_MS) {
    lastReport = millis();
    reportSensors();
  }
  delay(10);
}
