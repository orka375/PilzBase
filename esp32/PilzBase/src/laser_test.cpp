// PilzBase laser INPUT diagnostic (ESP32-S3, M5Stack Unit AIN4-20mA U162).
// Relay actuation requires explicitly configuring a VERIFIED GPIO below.
// Build only with: pio run -e laser-test -t upload
#include <Arduino.h>
#include <Wire.h>

constexpr uint8_t SDA_PIN = 8;
constexpr uint8_t SCL_PIN = 9;
constexpr uint8_t AIN_ADDRESS = 0x55;
constexpr unsigned long REPORT_EVERY_MS = 1000;

// CONFIRMED USER POLARITY: relay HIGH/true = laser ON, LOW/false = laser OFF.
// GPIO21 was proposed, NOT confirmed. Keep -1 until the actual connection
// and independent safe-off circuitry are checked.
constexpr int RELAY_GPIO = -1;
constexpr bool RELAY_HIGH_TURNS_LASER_ON = true;
constexpr unsigned long LASER_MAX_ON_MS = 10000; // Automatic timeout during tests.
bool laserOn = false;
unsigned long laserStartedAt = 0;

void setLaser(bool on) {
  if (RELAY_GPIO < 0) {
    Serial.println("Relay disabled: set RELAY_GPIO to the verified ESP32 pin first.");
    return;
  }
  laserOn = on;
  laserStartedAt = on ? millis() : 0;
  digitalWrite(RELAY_GPIO, (on == RELAY_HIGH_TURNS_LASER_ON) ? HIGH : LOW);
  Serial.println(on ? "Laser relay: ON (10-second auto-off)" : "Laser relay: OFF");
}

// Set these to your optoNCDT 1220's ACTUAL configured 4 mA / 20 mA endpoints.
// Leave false until you confirm the model and measurement range.
constexpr bool DISTANCE_CALIBRATED = false;
constexpr float DISTANCE_AT_4MA_MM = 0.0f;
constexpr float DISTANCE_AT_20MA_MM = 0.0f;

bool readRegister(uint8_t reg, uint8_t *out, size_t count) {
  Wire.beginTransmission(AIN_ADDRESS);
  Wire.write(reg);
  int txError = Wire.endTransmission(false);
  if (txError != 0) {
    Serial.printf("  I2C register 0x%02X write error: %d\n", reg, txError);
    return false;
  }
  size_t received = Wire.requestFrom((int)AIN_ADDRESS, (int)count);
  if (received != count) {
    Serial.printf("  I2C register 0x%02X read error: got %u/%u bytes\n",
                  reg, (unsigned)received, (unsigned)count);
    while (Wire.available()) Wire.read();
    return false;
  }
  for (size_t i = 0; i < count; i++) out[i] = Wire.read();
  return true;
}

uint16_t littleEndian16(const uint8_t *data) {
  return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

void reportLaserInput() {
  Serial.printf("\n--- Laser input test (%lu ms) ---\n", millis());
  Wire.beginTransmission(AIN_ADDRESS);
  int ack = Wire.endTransmission();
  if (ack != 0) {
    Serial.printf("M5Stack AIN4-20mA not responding at 0x%02X (I2C error %d)\n", AIN_ADDRESS, ack);
    Serial.println("Check 5V module power, GND, GPIO8 SDA, GPIO9 SCL, and 3.3V-safe I2C levels.");
    return;
  }

  uint8_t firmware = 0;
  if (readRegister(0xFE, &firmware, 1))
    Serial.printf("I2C device: 0x%02X, firmware register: 0x%02X\n", AIN_ADDRESS, firmware);

  uint8_t adcData[2] = {};
  uint8_t currentData[2] = {};
  bool adcOk = readRegister(0x00, adcData, 2);
  bool currentOk = readRegister(0x20, currentData, 2);
  if (adcOk) {
    const uint16_t raw = littleEndian16(adcData) & 0x0FFF;
    Serial.printf("ADC register [0x00,0x01]: 0x%02X 0x%02X -> %u / 4095\n",
                  adcData[0], adcData[1], raw);
  }
  if (!currentOk) {
    Serial.println("Current read failed. No distance calculation possible.");
    return;
  }

  const uint16_t centiMilliamps = littleEndian16(currentData);
  const float currentMA = centiMilliamps / 100.0f;
  Serial.printf("Current register [0x20,0x21]: 0x%02X 0x%02X -> %.2f mA\n",
                currentData[0], currentData[1], currentMA);

  if (currentMA < 3.6f) Serial.println("Status: BELOW 4mA range (open loop, no laser, or wiring fault?)");
  else if (currentMA > 20.5f) Serial.println("Status: ABOVE 20mA range (check loop/configuration)");
  else if (currentMA < 4.0f || currentMA > 20.0f)
    Serial.println("Status: NEAR range boundary; verify transmitter specifications");
  else Serial.println("Status: Within nominal 4-20mA span (not proof of valid distance)");

  if (DISTANCE_CALIBRATED && DISTANCE_AT_20MA_MM > DISTANCE_AT_4MA_MM &&
      currentMA >= 4.0f && currentMA <= 20.0f) {
    float mm = DISTANCE_AT_4MA_MM + (currentMA - 4.0f) / 16.0f *
               (DISTANCE_AT_20MA_MM - DISTANCE_AT_4MA_MM);
    Serial.printf("Distance: %.2f mm (calculated from configured endpoints)\n", mm);
  } else {
    Serial.println("Distance: NOT CALIBRATED (set endpoints only after verification)");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\nPilzBase ESP32-S3 LASER INPUT ONLY TEST");
  if (RELAY_GPIO >= 0) {
    // Apply the safe OFF level before making the GPIO an output.
    digitalWrite(RELAY_GPIO, RELAY_HIGH_TURNS_LASER_ON ? LOW : HIGH);
    pinMode(RELAY_GPIO, OUTPUT);
  }
  Serial.printf("Relay: %s; HIGH=true turns laser ON; boot state OFF\n",
                RELAY_GPIO >= 0 ? "serial-controlled" : "DISABLED");
  Serial.println("Serial commands: on, off, help. 'on' requires verified relay GPIO.");
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  Wire.setTimeOut(100);
}

void loop() {
  if (laserOn && millis() - laserStartedAt >= LASER_MAX_ON_MS) {
    Serial.println("Safety timeout: switching laser OFF");
    setLaser(false);
  }
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    command.toLowerCase();
    if (command == "on") setLaser(true);
    else if (command == "off") setLaser(false);
    else if (command == "help") Serial.println("on = energize for <=10s, off = disable, help = this text");
    else if (command.length()) Serial.println("Unknown command; type 'help'");
  }
  static unsigned long lastReport = 0;
  if (lastReport == 0 || millis() - lastReport >= REPORT_EVERY_MS) {
    lastReport = millis();
    reportLaserInput();
  }
  delay(10);
}
