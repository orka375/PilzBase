#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

class FlashLog {
public:
  bool begin();
  bool append(JsonDocument &record);
  void readBatch(uint32_t after, uint16_t limit, JsonArray output);
  bool acknowledge(uint32_t through);
  uint32_t nextId() const { return next_; }
  uint32_t acknowledged() const { return ack_; }
  String generation() const { return generation_; }
  size_t used() const;
  size_t capacity() const;
private:
  bool compact();
  uint32_t next_ = 1, ack_ = 0;
  bool needsCompact_ = false;
  String generation_;
};
