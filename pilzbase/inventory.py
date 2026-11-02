"""Non-invasive inventory and bounded bus diagnostics for the station dashboard.

I2C identification is by *address ACK only*, not a validated device identity.
Do not scan every address: a stuck SDA line can make every address appear.
"""

DEVICES = [
 ("pt100_1", "Temperature", "MAX31865 #1 + PT100", "SPI0 CE0 (proposed)", ["pt100_1_c"]),
 ("pt100_2", "Temperature", "MAX31865 #2 + PT100", "SPI0 CE1 (proposed)", ["pt100_2_c"]),
 ("dfr0553", "ADC", "DFRobot DFR0553 / ADS1115", "I²C1 0x48 (proposed)", []),
 ("opto_1220", "Distance", "Micro-Epsilon optoNCDT 1220", "DFR0553 A1 (proposed)", ["laser_distance_mm", "laser_current_ma"]),
 ("grove_soil", "Soil moisture", "Seeed Grove Soil Moisture Sensor", "DFR0553 A0 (proposed)", ["soil_voltage_v"]),
 ("bme280", "Environment", "Bosch BME280 (DFRobot module)", "I²C1 0x76", ["bme280_temperature_c", "bme280_humidity_pct", "bme280_pressure_hpa"]),
 ("ens160", "Air quality", "ScioSense ENS160 (DFRobot module)", "I²C1 0x53", ["ens160_aqi", "ens160_tvoc_ppb", "ens160_eco2_ppm"]),
 ("cjmcu_34725", "Color / light", "CJMCU-34725 / TCS34725", "I²C1 0x29", ["color_clear_raw", "color_red_raw", "color_green_raw", "color_blue_raw"]),
 ("scd41", "CO₂", "Sensirion SCD41", "I²C1 0x62", ["co2_ppm", "air_temperature_c", "humidity_rh_pct"]),
 ("ups_hat_c", "Power", "Waveshare UPS HAT (C) / INA219", "I²C1 0x43", ["battery_voltage_v", "battery_current_ma", "battery_power_w", "battery_capacity_est_pct"]),
]

def _bus_status(config):
    """Read-only, bounded I2C probe. Never write device registers."""
    from smbus import SMBus
    wanted = {}
    for key, _, _, _, _ in DEVICES:
        cfg = config.get("ups_hat_c", {}) if key == "ups_hat_c" else config.get("sensors", {}).get(key, {})
        if cfg.get("interface", "i2c") == "i2c" and key in ("dfr0553", "bme280", "ens160", "scd41", "cjmcu_34725", "ups_hat_c"):
            wanted[(int(cfg.get("bus", 1)), int(cfg.get("address", 0x43 if key == "ups_hat_c" else 0)))] = None
    for bus_id, address in list(wanted):
        try:
            with SMBus(bus_id) as bus:
                # A read byte is a read-only probe; not all devices support it.
                bus.read_byte(address)
            wanted[(bus_id, address)] = True
        except OSError:
            wanted[(bus_id, address)] = False
        except Exception:
            wanted[(bus_id, address)] = None
    return wanted

def inventory(config, mode, latest=None, probe=False):
    sensors = config.get("sensors", {})
    ups = config.get("ups_hat_c", {})
    checks = _bus_status(config) if probe else {}
    values = (latest or {}).get("values", {})
    errors = (latest or {}).get("errors", {})
    rows = []
    for key, kind, model, default_connection, fields in DEVICES:
        cfg = ups if key == "ups_hat_c" else sensors.get(key, {})
        enabled = bool(cfg.get("enabled", False))
        if key in ("opto_1220", "grove_soil"):
            enabled = enabled and bool(sensors.get("dfr0553", {}).get("enabled"))
        connection = default_connection
        if key in ("dfr0553", "bme280", "ens160", "scd41", "cjmcu_34725", "ups_hat_c"):
            address = int(cfg.get("address", {"dfr0553":0x48,"bme280":0x76,"ens160":0x53,"scd41":0x62,"cjmcu_34725":0x29,"ups_hat_c":0x43}[key]))
            bus = int(cfg.get("bus", 1))
            connection = f"I²C{bus} 0x{address:02X}"
        elif key.startswith("pt100_"):
            connection = f"SPI{cfg.get('bus',0)} CE{cfg.get('device',0)} (verify wiring)"
        elif key in ("opto_1220", "grove_soil"):
            connection = f"DFR0553 A{cfg.get('adc_channel', '?')} (verify wiring)"
        available = {field: values.get(field) for field in fields if values.get(field) is not None}
        if latest and latest.get("simulated") and key != "ups_hat_c":
            available = {}
        if mode == "simulated" and key != "ups_hat_c":
            available = {}
        if mode == "hardware" and not enabled:
            available = {}
        # Do not claim detection based on simulated values or unverified address ACK.
        if mode == "simulated" and key != "ups_hat_c":
            status = "disabled" if not enabled else "not_checked"
            detail = "Simulated mode: physical sensor not polled"
        elif not enabled:
            status, detail = "disabled", "Not enabled in hardware.yaml"
        elif available and (mode == "hardware" or key == "ups_hat_c"):
            status, detail = "reading", "Recent valid measurement; physical identity not independently verified"
        elif key in errors:
            status, detail = "error", str(errors[key])
        else:
            status, detail = "not_checked", "No successful physical measurement yet"
        if probe and key in ("dfr0553", "bme280", "ens160", "scd41", "cjmcu_34725", "ups_hat_c"):
            ack = checks.get((int(cfg.get("bus",1)), int(cfg.get("address",{"dfr0553":0x48,"bme280":0x76,"ens160":0x53,"scd41":0x62,"cjmcu_34725":0x29,"ups_hat_c":0x43}[key]))))
            if ack is True:
                detail += "; I²C address responded (identity unverified)"
            elif ack is False:
                detail += "; I²C read-byte probe failed (not conclusive)"
        rows.append({"id":key,"type":kind,"model":model,"connection":connection,
                     "enabled":enabled,"status":status,"detail":detail,"values":available})
    return {"sensor_mode":mode,"sensors":rows,"probe_warning":"I²C address ACK is not proof of identity. All-address scans indicate a bus fault."}
