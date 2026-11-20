#pragma once
#include <Arduino.h>
// Configure by PlatformIO build flags or an ignored local header.
#if __has_include("local_settings.h")
#include "local_settings.h"
#endif
#ifndef PILZ_WIFI_SSID
#define PILZ_WIFI_SSID ""
#endif
#ifndef PILZ_WIFI_PASSWORD
#define PILZ_WIFI_PASSWORD ""
#endif
#ifndef PILZ_STATION_ID
#define PILZ_STATION_ID "UNIT-01"
#endif
#ifndef PILZ_API_TOKEN
#define PILZ_API_TOKEN ""
#endif
constexpr uint32_t SAMPLE_INTERVAL_MS = 15000;
constexpr uint32_t EXPERIMENT_DURATION_S = 56UL * 24UL * 3600UL;
