#pragma once
// ESP32-S3 N8R8: GPIO 35-37 are occupied by octal PSRAM.
constexpr int I2C_SDA = 8, I2C_SCL = 9;
constexpr int SPI_SCK_PIN = 38, SPI_MISO_PIN = 40, SPI_MOSI_PIN = 39;
constexpr int PT100_CS1 = 10, PT100_CS2 = 14;
constexpr int MOISTURE_PIN = 5;
// GPIO41 is the verified laser-control output, NOT a status LED.
// LED and button pin assignments remain unconfirmed.
constexpr int LED_PIN = -1, BUTTON_PIN = -1, RELAY_PIN = 41;
constexpr bool BUTTON_ACTIVE_LOW = true, LED_ACTIVE_HIGH = true;
