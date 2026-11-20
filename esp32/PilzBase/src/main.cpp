#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <Adafruit_MAX31865.h>
#include <Adafruit_TCS34725.h>
#include <Adafruit_BME280.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <time.h>
#include "pins.h"
#include "settings.h"
#include "FlashLog.h"

// ESP32-S3 N8R8: GPIO35/36/37 belong to octal PSRAM. DO NOT use them for CS.
constexpr float RTD_NOMINAL = 100.0f;    // PT100; use 1000.0 for PT1000
constexpr float REF_RESISTOR = 430.0f;   // Adafruit PT100 boards normally 430 ohms
constexpr max31865_numwires_t RTD_WIRES = MAX31865_3WIRE;
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
WebServer server(8000);
FlashLog logStore;
Preferences experimentPrefs;
JsonDocument latest;
uint32_t lastSample = 0, lastWiFiAttempt = 0;
bool flashReady = false, measuring = false;
uint32_t experimentId = 0, experimentEnd = 0;
const char *firmwareVersion = "0.3.0-esp32";

bool clockValid() { return time(nullptr) > 1700000000; }
String isoNow() {
  if (!clockValid()) return String();
  time_t now = time(nullptr);
  struct tm utc;
  gmtime_r(&now, &utc);
  char buffer[25];
  strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &utc);
  return buffer;
}
void reply(int code, JsonDocument &doc) {
  String body;
  serializeJson(doc, body);
  server.send(code, "application/json", body);
}
bool authorized() {
  if (strlen(PILZ_API_TOKEN) < 12 || server.header("X-Pilz-Token") != PILZ_API_TOKEN) {
    server.send(403, "application/json", "{\"error\":\"API token required\"}");
    return false;
  }
  return true;
}

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

void collectSensors(JsonDocument &doc) {
  doc.clear();
  JsonObject values = doc["values"].to<JsonObject>();
  JsonObject errors = doc["errors"].to<JsonObject>();
  auto rtd = [&](const char *key, Adafruit_MAX31865 &sensor, bool enabled) {
    if (!enabled) { errors[key] = "SPI initialization failed"; return; }
    sensor.clearFault();
    uint16_t raw = sensor.readRTD();
    uint8_t fault = sensor.readFault();
    float ohms = raw * REF_RESISTOR / 32768.0f;
    if (fault || raw == 0 || ohms < 60 || ohms > 180) {
      errors[key] = String("RTD fault=") + String(fault, HEX) + ", raw=" + raw;
    } else values[key] = sensor.calculateTemperature(raw, RTD_NOMINAL, REF_RESISTOR);
  };
  rtd("pt100_1_c", rtd1, hasRtd1);
  rtd("pt100_2_c", rtd2, hasRtd2);
  if (hasTCS) {
    uint16_t r, g, b, c;
    tcs.getRawData(&r, &g, &b, &c);
    values["color_red_raw"] = r; values["color_green_raw"] = g;
    values["color_blue_raw"] = b; values["color_clear_raw"] = c;
  } else errors["cjmcu_34725"] = "TCS34725 unavailable";
  if (hasBME) {
    float t = bme.readTemperature(), h = bme.readHumidity(), p = bme.readPressure() / 100.0f;
    if (isfinite(t) && isfinite(h) && isfinite(p)) {
      values["bme280_temperature_c"] = t;
      values["bme280_humidity_pct"] = h;
      values["bme280_pressure_hpa"] = p;
    } else errors["bme280"] = "Invalid sensor values";
  } else errors["bme280"] = "BME280 unavailable";
  if (hasSCD) {
    uint16_t ready = 0;
    if (!scdWords(0xE4B8, &ready, 1)) errors["scd41"] = "Data-ready command failed";
    else if (ready & 0x07ff) {
      uint16_t data[3];
      if (scdWords(0xEC05, data, 3)) {
        values["co2_ppm"] = data[0];
        values["air_temperature_c"] = -45.0f + 175.0f * data[1] / 65536.0f;
        values["humidity_rh_pct"] = 100.0f * data[2] / 65536.0f;
      } else errors["scd41"] = "Measurement read failed";
    } else errors["scd41"] = "Measurement not ready";
  } else errors["scd41"] = "SCD41 unavailable";
  if (hasENS) {
    uint8_t data[6];
    if (readRegs(ensAddress, 0x20, data, sizeof(data))) {
      uint8_t validity = (data[0] >> 2) & 3;
      if (validity == 0) {
        values["ens160_aqi"] = data[1];
        values["ens160_tvoc_ppb"] = le16(data + 2);
        values["ens160_eco2_ppm"] = le16(data + 4);
      } else errors["ens160"] = String("Warm-up/status=") + validity;
    } else errors["ens160"] = "I2C read failed";
  } else errors["ens160"] = "ENS160 unavailable";
  values["soil_voltage_v"] = analogReadMilliVolts(MOISTURE_PIN) / 1000.0f;
  if (hasM5) {
    uint8_t data[2];
    if (readRegs(ADDR_M5, 0x20, data, 2)) values["laser_current_ma"] = le16(data) / 100.0f;
    else errors["m5_ain"] = "Current-loop read failed";
  } else errors["m5_ain"] = "M5 AIN unavailable";
  // INA219 UPS HAT C: voltage is the monitored bus, NOT the cell voltage.
  // Do not provide misleading battery percentage or voltage if its bus is ~0.46 V.
  if (i2cPresent(0x43)) {
    uint8_t data[2];
    if (readRegs(0x43, 0x02, data, 2)) {
      float busV = ((be16(data) >> 3) * 0.004f);
      if (busV >= 2.5f && busV <= 6.0f) {
        values["ups_bus_voltage_v"] = busV;
        if (readRegs(0x43, 0x01, data, 2))
          values["ups_shunt_voltage_mv"] = static_cast<int16_t>(be16(data)) * 0.01f;
      }
      else errors["ups_hat_c"] = "INA219 bus voltage implausible; check HAT wiring";
    } else errors["ups_hat_c"] = "INA219 communication failed";
  } else errors["ups_hat_c"] = "INA219 unavailable";
  errors["opto_1220"] = "Laser disabled pending relay and 4-20mA calibration";
}

void takeMeasurement() {
  if (!flashReady) return;
  JsonDocument doc;
  collectSensors(doc);
  latest = doc;
  doc["experiment_id"] = experimentId;
  if (clockValid()) doc["acquired_at"] = isoNow();
  else { doc["acquired_at"] = nullptr; doc["errors"]["clock"] = "NTP time unavailable"; }
  doc["uptime_ms"] = millis();
  if (!logStore.append(doc)) {
    Serial.println("[ERROR] Flash full or write failed; recording paused to protect journal");
    flashReady = false;
  }
}

void setupApi() {
  const char *headers[] = {"X-Pilz-Token"};
  server.collectHeaders(headers, 1);
  server.on("/api/v1/status", HTTP_GET, []() {
    JsonDocument doc;
    doc["station_id"] = PILZ_STATION_ID;
    doc["state"] = measuring ? "MEASURING" : "READY";
    doc["sensor_mode"] = "hardware";
    doc["firmware"] = firmwareVersion;
    doc["storage_generation"] = logStore.generation();
    doc["measurements"] = logStore.nextId() - 1;
    doc["pending_records"] = logStore.nextId() - logStore.acknowledged() - 1;
    doc["flash_used_bytes"] = logStore.used();
    doc["flash_total_bytes"] = logStore.capacity();
    doc["wifi_rssi_dbm"] = WiFi.RSSI();
    doc["clock_valid"] = clockValid();
    doc["storage_ok"] = flashReady;
    reply(200, doc);
  });
  server.on("/api/v1/sensors", HTTP_GET, []() {
    JsonDocument doc;
    doc["sensor_mode"] = "hardware";
    JsonArray sensors = doc["sensors"].to<JsonArray>();
    struct Entry { const char *name, *model, *kind, *connection, *fields; };
    const Entry entries[] = {
      {"pt100_1", "MAX31865 #1 + PT100", "Temperature", "SPI CS GPIO10", "pt100_1_c"},
      {"pt100_2", "MAX31865 #2 + PT100", "Temperature", "SPI CS GPIO14", "pt100_2_c"},
      {"scd41", "Sensirion SCD41", "CO2", "I2C 0x62", "co2_ppm,air_temperature_c,humidity_rh_pct"},
      {"bme280", "Bosch BME280", "Environment", "I2C 0x76/0x77", "bme280_temperature_c,bme280_humidity_pct,bme280_pressure_hpa"},
      {"ens160", "ScioSense ENS160", "Air quality", "I2C 0x52/0x53", "ens160_aqi,ens160_tvoc_ppb,ens160_eco2_ppm"},
      {"cjmcu_34725", "CJMCU-34725 / TCS34725", "Color", "I2C 0x29", "color_red_raw,color_green_raw,color_blue_raw,color_clear_raw"},
      {"grove_soil", "Grove Soil Moisture", "Moisture", "ADC GPIO5", "soil_voltage_v"},
      {"m5_ain", "M5Stack AIN 4-20mA", "Current", "I2C 0x55", "laser_current_ma"},
      {"ups_hat_c", "Waveshare UPS HAT (C) / INA219", "Power", "I2C 0x43", "ups_bus_voltage_v,ups_shunt_voltage_mv"},
      {"opto_1220", "Micro-Epsilon optoNCDT 1220", "Distance", "M5 AIN / relay unverified", "laser_distance_mm"}
    };
    for (auto &entry : entries) {
      JsonObject row = sensors.add<JsonObject>();
      row["id"] = entry.name; row["model"] = entry.model;
      row["type"] = entry.kind; row["connection"] = entry.connection;
      JsonObject values = row["values"].to<JsonObject>();
      String keys = entry.fields;
      while (keys.length()) {
        int comma = keys.indexOf(',');
        String key = comma < 0 ? keys : keys.substring(0, comma);
        if (!latest["values"][key].isNull()) values[key] = latest["values"][key];
        if (comma < 0) break;
        keys.remove(0, comma + 1);
      }
      bool disabled = String(entry.name) == "opto_1220";
      String errorKey = entry.name;
      if (errorKey == "pt100_1" || errorKey == "pt100_2") errorKey += "_c";
      row["enabled"] = !disabled;
      row["status"] = disabled ? "disabled" : !latest["errors"][errorKey].isNull() ? "error" : values.size() ? "reading" : "not_checked";
      row["detail"] = disabled ? "Laser disabled" : latest["errors"][errorKey].as<String>();
    }
    reply(200, doc);
  });
  server.on("/api/v1/measurements", HTTP_GET, []() {
    uint32_t after = max(0, server.arg("after_id").toInt());
    uint16_t limit = constrain(server.arg("limit").toInt(), 1, 100);
    JsonDocument doc;
    doc["station_id"] = PILZ_STATION_ID;
    doc["storage_generation"] = logStore.generation();
    JsonArray rows = doc["rows"].to<JsonArray>();
    if (flashReady) logStore.readBatch(after, limit, rows);
    reply(200, doc);
  });
  server.on("/api/v1/measurements/ack", HTTP_POST, []() {
    if (!authorized()) return;
    JsonDocument data;
    if (deserializeJson(data, server.arg("plain")) || data["generation"].as<String>() != logStore.generation()
        || !data["through_id"].is<uint32_t>()) {
      server.send(400, "application/json", "{\"error\":\"Invalid acknowledgment\"}"); return;
    }
    if (!logStore.acknowledge(data["through_id"].as<uint32_t>())) {
      server.send(409, "application/json", "{\"error\":\"ACK or compaction failed\"}"); return;
    }
    JsonDocument doc; doc["acknowledged"] = logStore.acknowledged(); reply(200, doc);
  });
  server.on("/api/v1/experiments/start", HTTP_POST, []() {
    if (!authorized()) return;
    if (!flashReady || !clockValid() || measuring) { server.send(409, "application/json", "{\"error\":\"Storage/clock unavailable or already running\"}"); return; }
    ++experimentId;
    experimentEnd = time(nullptr) + EXPERIMENT_DURATION_S;
    measuring = true;
    experimentPrefs.putULong("id", experimentId);
    experimentPrefs.putULong("end", experimentEnd);
    experimentPrefs.putBool("running", true);
    JsonDocument doc; doc["state"] = "MEASURING"; doc["experiment_id"] = experimentId; reply(200, doc);
  });
  server.on("/api/v1/experiments/stop", HTTP_POST, []() {
    if (!authorized()) return;
    measuring = false;
    experimentPrefs.putBool("running", false);
    JsonDocument doc; doc["state"] = "STOPPED"; reply(200, doc);
  });
  server.begin();
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\nESP32-S3 multi-sensor diagnostic (Arduino + PlatformIO)");
  if (LED_PIN >= 0) { pinMode(LED_PIN, OUTPUT); digitalWrite(LED_PIN, LED_ACTIVE_HIGH ? LOW : HIGH); }
  if (BUTTON_PIN >= 0) pinMode(BUTTON_PIN, BUTTON_ACTIVE_LOW ? INPUT_PULLUP : INPUT_PULLDOWN);
  analogReadResolution(12);
  analogSetPinAttenuation(MOISTURE_PIN, ADC_11db);
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);
  Wire.setTimeOut(100);
  scanI2C();
  initSensors();
  flashReady = logStore.begin();
  experimentPrefs.begin("pilzexp", false);
  experimentId = experimentPrefs.getULong("id", 0);
  experimentEnd = experimentPrefs.getULong("end", 0);
  measuring = experimentPrefs.getBool("running", false);
  WiFi.mode(WIFI_STA);
  if (strlen(PILZ_WIFI_SSID)) WiFi.begin(PILZ_WIFI_SSID, PILZ_WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org");
  setupApi();
  lastReport = millis() - REPORT_INTERVAL_MS;
}

void loop() {
  // Toggle external LED once per second; if LED is RGB/addressable, use its own driver instead.
  static uint32_t lastBlink = 0;
  static bool ledOn = false;
  if (LED_PIN >= 0 && millis() - lastBlink >= 1000) {
    lastBlink = millis();
    ledOn = !ledOn;
    digitalWrite(LED_PIN, (ledOn == LED_ACTIVE_HIGH) ? HIGH : LOW);
  }
  if (millis() - lastReport >= REPORT_INTERVAL_MS) {
    lastReport = millis();
    reportSensors();
  }
  server.handleClient();
  if (WiFi.status() != WL_CONNECTED && millis() - lastWiFiAttempt > 15000) {
    lastWiFiAttempt = millis();
    if (strlen(PILZ_WIFI_SSID)) WiFi.reconnect();
  }
  if (measuring && clockValid() && time(nullptr) >= experimentEnd) {
    measuring = false;
    experimentPrefs.putBool("running", false);
  }
  if (measuring && millis() - lastSample >= SAMPLE_INTERVAL_MS) {
    lastSample = millis();
    takeMeasurement();
  }
  delay(10);
}
