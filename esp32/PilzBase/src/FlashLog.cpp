#include "FlashLog.h"
#include <LittleFS.h>
#include <Preferences.h>

// Append-only NDJSON journal; a torn final line is discarded at boot.
// Acknowledged records are reclaimed with a temporary file + rename.
// Limits are conservative because LittleFS partitions vary by board.
namespace {
constexpr const char *LOG = "/records.log", *TEMP = "/records.tmp";
Preferences prefs;
}

bool FlashLog::begin() {
  prefs.begin("pilzlog", false);
  // Never silently format existing data after a filesystem mount failure.
  if (!LittleFS.begin(false)) {
    if (prefs.isKey("generation") || !LittleFS.begin(true)) return false;
  }
  generation_ = prefs.getString("generation", "");
  if (generation_.isEmpty()) {
    generation_ = String((uint32_t)esp_random(), HEX) + String((uint32_t)esp_random(), HEX);
    prefs.putString("generation", generation_);
  }
  ack_ = prefs.getULong("ack", 0);
  next_ = prefs.getULong("next", 1);
  if (LittleFS.exists(TEMP)) LittleFS.remove(TEMP); // Never replace valid log with incomplete copy.
  File file = LittleFS.open(LOG, "r");
  if (!file) return true;
  bool corruptTail = false;
  while (file.available()) {
    String line = file.readStringUntil('\n');
    if (!line.endsWith("}")) { corruptTail = true; break; }
    JsonDocument doc;
    if (deserializeJson(doc, line) || !doc["id"].is<uint32_t>()) { corruptTail = true; break; }
    next_ = max(next_, doc["id"].as<uint32_t>() + 1);
  }
  file.close();
  // Remove an incomplete trailing record before appending another valid one.
  if (corruptTail && !compact()) return false;
  prefs.putULong("next", next_);
  return true;
}

size_t FlashLog::used() const { return LittleFS.usedBytes(); }
size_t FlashLog::capacity() const { return LittleFS.totalBytes(); }

bool FlashLog::append(JsonDocument &record) {
  // Never overwrite unacknowledged data. Reserve one erase block as headroom.
  if (capacity() < used() + 8192) return false;
  record["id"] = next_;
  String line;
  serializeJson(record, line);
  File file = LittleFS.open(LOG, "a");
  if (!file) return false;
  bool ok = file.print(line) == line.length() && file.print('\n') == 1;
  file.flush();
  file.close();
  if (!ok) return false;
  ++next_;
  return true;
}

void FlashLog::readBatch(uint32_t after, uint16_t limit, JsonArray output) {
  File file = LittleFS.open(LOG, "r");
  if (!file) return;
  while (file.available() && output.size() < limit) {
    String line = file.readStringUntil('\n');
    JsonDocument record;
    if (deserializeJson(record, line)) break;
    uint32_t id = record["id"] | 0;
    if (id > after && id > ack_) output.add(record.as<JsonVariant>());
  }
  file.close();
}

bool FlashLog::compact() {
  File source = LittleFS.open(LOG, "r");
  if (!source) return true;
  File target = LittleFS.open(TEMP, "w");
  if (!target) { source.close(); return false; }
  bool ok = true;
  while (source.available()) {
    String line = source.readStringUntil('\n');
    JsonDocument record;
    if (deserializeJson(record, line)) break;
    if ((record["id"] | 0U) > ack_) {
      if (target.print(line) != line.length() || target.print('\n') != 1) { ok = false; break; }
    }
  }
  target.flush(); target.close(); source.close();
  if (!ok) { LittleFS.remove(TEMP); return false; }
  // LittleFS rename is atomic within the same filesystem.
  if (!LittleFS.rename(TEMP, LOG)) { LittleFS.remove(TEMP); return false; }
  return true;
}

bool FlashLog::acknowledge(uint32_t through) {
  if (through <= ack_) {
    if (!needsCompact_) return true;
    needsCompact_ = !compact();
    return !needsCompact_;
  }
  if (through >= next_) return false;
  ack_ = through;
  prefs.putULong("next", next_); // Persist ID sequence before any records are removed.
  prefs.putULong("ack", ack_); // Persist before reclaiming files.
  needsCompact_ = !compact();
  return !needsCompact_;
}
