
#include <Arduino.h>
#include <SPI.h>

// ESP32-S3 SPI pins
#define SPI_MISO 40
#define SPI_MOSI 39
#define SPI_CLK  38

#define CS1 10
#define CS2 14

SPIClass spi(FSPI);

// MAX31865 registers
#define REG_CONFIG 0x00
#define REG_RTD_MSB 0x01
#define REG_FAULT 0x07

SPISettings settings(100000, MSBFIRST, SPI_MODE1);

// Read MAX31865 register
uint8_t readRegister(uint8_t cs, uint8_t reg) {
  spi.beginTransaction(settings);

  digitalWrite(cs, LOW);
  spi.transfer(reg & 0x7F);
  uint8_t value = spi.transfer(0x00);
  digitalWrite(cs, HIGH);

  spi.endTransaction();

  return value;
}

// Write MAX31865 register
void writeRegister(uint8_t cs, uint8_t reg, uint8_t value) {
  spi.beginTransaction(settings);

  digitalWrite(cs, LOW);
  spi.transfer(reg | 0x80);
  spi.transfer(value);
  digitalWrite(cs, HIGH);

  spi.endTransaction();
}

// Read 16-bit RTD register
uint16_t readRTD(uint8_t cs) {
  spi.beginTransaction(settings);

  digitalWrite(cs, LOW);
  spi.transfer(REG_RTD_MSB);
  uint8_t msb = spi.transfer(0x00);
  uint8_t lsb = spi.transfer(0x00);
  digitalWrite(cs, HIGH);

  spi.endTransaction();

  return ((uint16_t)msb << 8) | lsb;
}

void debugMAX31865(uint8_t cs, const char* name) {
  Serial.println();
  Serial.println("========================");
  Serial.println(name);
  Serial.println("========================");

  // Read original configuration
  uint8_t config = readRegister(cs, REG_CONFIG);

  Serial.printf("Initial config: 0x%02X\n", config);

  // Enable bias, 50 Hz filter
  writeRegister(cs, REG_CONFIG, 0x81);

  delay(20);

  config = readRegister(cs, REG_CONFIG);

  Serial.printf("Config after write: 0x%02X\n", config);

  if (config != 0x81) {
    Serial.println("[FAIL] SPI register readback!");
    Serial.println("Check MISO, MOSI, CLK, CS and power.");
    return;
  }

  Serial.println("[OK] SPI register readback successful");

  // Enable continuous conversion
  // Bias ON, automatic conversion, 50 Hz filter
  writeRegister(cs, REG_CONFIG, 0xC1);

  delay(200);

  config = readRegister(cs, REG_CONFIG);
  Serial.printf("Conversion config: 0x%02X\n", config);

  if (config != 0xC1) {
    Serial.println("[FAIL] Cannot enable conversion");
    return;
  }

  // Read RTD
  uint16_t raw16 = readRTD(cs);
  uint16_t raw15 = raw16 >> 1;

  Serial.printf("RTD 16-bit: 0x%04X\n", raw16);
  Serial.printf("RTD raw:    %u\n", raw15);

  // Read fault register
  uint8_t fault = readRegister(cs, REG_FAULT);

  Serial.printf("Fault: 0x%02X\n", fault);

  if (raw15 == 0) {
    Serial.println("[WARN] RTD value is ZERO");
  } else {
    // Assuming Adafruit PT100 with 430 ohm RREF
    float resistance = raw15 * 430.0 / 32768.0;

    Serial.printf("Resistance: %.2f Ohm\n", resistance);

    if (resistance > 80 && resistance < 140) {
      Serial.println("[OK] Plausible PT100 resistance");
    } else {
      Serial.println("[WARN] Check PT100 wiring / RREF");
    }
  }

  if (fault != 0) {
    Serial.println("[WARN] Sensor reports a fault");
  }


  Serial.printf("High threshold MSB: 0x%02X\n",
              readRegister(cs, 0x03));

    Serial.printf("High threshold LSB: 0x%02X\n",
                readRegister(cs, 0x04));

    Serial.printf("Low threshold MSB:  0x%02X\n",
                readRegister(cs, 0x05));

    Serial.printf("Low threshold LSB:  0x%02X\n",
                readRegister(cs, 0x06));
    }

void setup() {
  Serial.begin(115200);
  delay(2000);

  Serial.println();
  Serial.println("ESP32-S3 MAX31865 SPI DEBUG");

  // Set both chip selects HIGH before SPI starts
  digitalWrite(CS1, HIGH);
  digitalWrite(CS2, HIGH);

  pinMode(CS1, OUTPUT);
  pinMode(CS2, OUTPUT);

  spi.begin(SPI_CLK, SPI_MISO, SPI_MOSI, -1);

  Serial.printf("MISO: GPIO %d\n", SPI_MISO);
  Serial.printf("MOSI: GPIO %d\n", SPI_MOSI);
  Serial.printf("CLK:  GPIO %d\n", SPI_CLK);
  Serial.printf("CS1:  GPIO %d\n", CS1);
  Serial.printf("CS2:  GPIO %d\n", CS2);
}

void loop() {
  debugMAX31865(CS1, "MAX31865 #1");
  debugMAX31865(CS2, "MAX31865 #2");

  delay(3000);
}
