"""Optional Raspberry Pi sensor readers; disabled channels return None, never fake data.

All bus operations are synchronous and called in the engine's worker thread.
Verify wiring, ADC voltage limits and calibration before enabling.
"""
import math
import time
from contextlib import closing


def ads1115_voltage(cfg, channel):
    from smbus import SMBus
    bus_id, addr = int(cfg.get("bus", 1)), int(cfg.get("address", 0x48))
    if channel not in range(4):
        raise ValueError("ADS1115 channel must be A0..A3")
    # Single-shot, single-ended channel, PGA ±4.096V, 128 SPS.
    config = 0x8000 | ((4 + channel) << 12) | 0x0200 | 0x0100 | 0x0083
    with closing(SMBus(bus_id)) as bus:
        bus.write_i2c_block_data(addr, 0x01, [(config >> 8) & 255, config & 255])
        time.sleep(0.012)
        hi, lo = bus.read_i2c_block_data(addr, 0x00, 2)
    raw = (hi << 8) | lo
    if raw & 0x8000:
        raw -= 65536
    return raw * 4.096 / 32768


def max31865_c(cfg):
    import spidev
    spi = spidev.SpiDev()
    try:
        spi.open(int(cfg.get("bus", 0)), int(cfg.get("device", 0)))
        spi.max_speed_hz = 500000
        spi.mode = 1
        # Continuous conversion, clear faults, 50 Hz notch, VBIAS enabled.
        setting = 0xC2 | (0x10 if int(cfg.get("wires", 3)) == 3 else 0x00)
        spi.xfer2([0x80, setting])
        time.sleep(0.07)
        data = spi.xfer2([0x01, 0x00, 0x00])
        raw = ((data[1] << 8) | data[2]) >> 1
        if data[2] & 1:
            raise IOError("MAX31865 RTD fault flag set")
        rref = float(cfg.get("reference_ohms", 430))
        nominal = float(cfg.get("rtd_nominal_ohms", 100))
        if rref <= 0 or nominal <= 0:
            raise ValueError("MAX31865 reference_ohms and rtd_nominal_ohms must be positive")
        r = raw * rref / 32768
        # Reject obvious wiring, wrong-RREF and chip-select errors instead of
        # presenting absurd temperatures as successful measurements.
        if not 0.75 * nominal <= r <= 1.7 * nominal:
            raise ValueError(f"Implausible PT100 resistance {r:.2f} ohm (raw={raw}, RREF={rref:g}); check wiring, SPI chip-select and reference_ohms")
        # Callendar–Van Dusen for >=0 C; negative temperatures not
        # supported by this approximation, fail explicitly instead.
        a, b = 3.9083e-3, -5.775e-7
        discr = a*a - 4*b*(1-r/nominal)
        if discr < 0:
            raise ValueError("Invalid PT100 resistance")
        temp = (-a + math.sqrt(discr)) / (2*b)
        if temp < 0:
            raise ValueError("Subzero PT100 requires extended CVD solver")
        return round(temp, 3)
    finally:
        spi.close()


def bme280_read(cfg):
    from smbus import SMBus
    import bme280
    with closing(SMBus(int(cfg.get("bus", 1)))) as bus:
        address = int(cfg.get("address", 0x76))
        calibration = bme280.load_calibration_params(bus, address)
        sample = bme280.sample(bus, address, calibration)
    return {"bme280_temperature_c": sample.temperature,
            "bme280_humidity_pct": sample.humidity,
            "bme280_pressure_hpa": sample.pressure}


def ens160_read(cfg):
    from smbus import SMBus
    with closing(SMBus(int(cfg.get("bus", 1)))) as bus:
        addr = int(cfg.get("address", 0x53))
        # ENS160 OPMODE: 0x02 = standard operation.
        bus.write_byte_data(addr, 0x10, 0x02)
        # DATA_STATUS 0x20; data validity is bits [3:2].
        status = bus.read_byte_data(addr, 0x20)
        if ((status >> 2) & 3) != 0:
            raise IOError(f"ENS160 data invalid / warming up (status=0x{status:02x})")
        aqi = bus.read_byte_data(addr, 0x21)
        tvoc = bus.read_word_data(addr, 0x22)
        eco2 = bus.read_word_data(addr, 0x24)
    if not (1 <= aqi <= 5 and 400 <= eco2 <= 65000):
        raise IOError(f"ENS160 implausible air quality (AQI={aqi}, TVOC={tvoc}, eCO2={eco2}, status=0x{status:02x})")
    return {"ens160_aqi": aqi, "ens160_tvoc_ppb": tvoc, "ens160_eco2_ppm": eco2}


def scd41_read(cfg):
    """SCD41 periodic reading; using smbus2 for raw I2C command frames."""
    from smbus2 import SMBus, i2c_msg
    addr = int(cfg.get("address", 0x62))
    def crc(data):
        v = 0xff
        for x in data:
            v ^= x
            for _ in range(8):
                v = ((v << 1) ^ 0x31) & 255 if v & 0x80 else (v << 1) & 255
        return v
    with SMBus(int(cfg.get("bus", 1))) as bus:
        # Start periodic measurement; harmless if already measuring, may NACK.
        # Do not restart every cycle: caller handles initialization separately.
        bus.i2c_rdwr(i2c_msg.write(addr, [0xe4, 0xb8]))
        time.sleep(0.002)
        ready = i2c_msg.read(addr, 3)
        bus.i2c_rdwr(ready)
        raw = list(ready)
        if raw[2] != crc(raw[:2]):
            raise IOError("SCD41 ready CRC mismatch")
        if (raw[0] << 8 | raw[1]) & 0x07ff == 0:
            return {}
        bus.i2c_rdwr(i2c_msg.write(addr, [0xec, 0x05]))
        time.sleep(0.002)
        result = i2c_msg.read(addr, 9)
        bus.i2c_rdwr(result)
        values = list(result)
        for pos in (0, 3, 6):
            if values[pos+2] != crc(values[pos:pos+2]):
                raise IOError("SCD41 measurement CRC mismatch")
        word = lambda idx: (values[idx] << 8) | values[idx+1]
        return {"co2_ppm": word(0),
                "air_temperature_c": -45+175*word(3)/65535,
                "humidity_rh_pct": 100*word(6)/65535}

def tcs34725_read(cfg):
    """Read physical RGBC counts with the working CircuitPython driver.

    The driver handles device-specific initialization and integration timing.
    Do not turn an I2C address ACK into a claimed physical measurement.
    """
    import board
    import adafruit_tcs34725

    # board.I2C() is the Pi's primary I2C bus (bus 1).
    if int(cfg.get("bus", 1)) != 1:
        raise ValueError("TCS34725 CircuitPython reader requires I2C bus 1")
    if int(cfg.get("address", 0x29)) != 0x29:
        raise ValueError("TCS34725 CircuitPython reader expects address 0x29")
    sensor = adafruit_tcs34725.TCS34725(board.I2C())
    red, green, blue, clear = sensor.color_raw
    return {"color_clear_raw": clear, "color_red_raw": red,
            "color_green_raw": green, "color_blue_raw": blue}


class PhysicalSensors:
    def __init__(self, config):
        self.cfg = config
        self.sensors = config.get("sensors", {})
        self.errors = {}
        self._scd_started = False
        self._scd_started_at = 0.0

    def read(self):
        values = {}
        errors = {}
        def attempt(name, call):
            try:
                values.update(call())
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"[:250]

        for idx in (1, 2):
            key = f"pt100_{idx}"
            cfg = self.sensors.get(key, {})
            if cfg.get("enabled"):
                attempt(key, lambda cfg=cfg, idx=idx: {f"pt100_{idx}_c": max31865_c(cfg)})

        adc = self.sensors.get("dfr0553", {})
        if adc.get("enabled"):
            soil = self.sensors.get("grove_soil", {})
            if soil.get("enabled"):
                attempt("grove_soil", lambda: {
                    "soil_voltage_v": ads1115_voltage(adc, int(soil["adc_channel"]))})
            laser = self.sensors.get("opto_1220", {})
            if laser.get("enabled"):
                def laser_read():
                    volts = ads1115_voltage(adc, int(laser["adc_channel"]))
                    ma = volts / float(laser["shunt_ohms"]) * 1000
                    if not (float(laser["min_ma"]) - 0.5 <= ma <= float(laser["max_ma"]) + 0.5):
                        raise ValueError(f"Laser current out of range: {ma:.2f}mA")
                    mm = float(laser["min_mm"]) + (ma-float(laser["min_ma"])) / (float(laser["max_ma"])-float(laser["min_ma"])) * (float(laser["max_mm"])-float(laser["min_mm"]))
                    return {"laser_distance_mm": mm, "laser_current_ma": ma}
                attempt("opto_1220", laser_read)

        for name, reader in (("bme280", bme280_read), ("ens160", ens160_read)):
            cfg = self.sensors.get(name, {})
            if cfg.get("enabled"):
                attempt(name, lambda cfg=cfg, reader=reader: reader(cfg))
        color = self.sensors.get("cjmcu_34725", {})
        if color.get("enabled"):
            attempt("cjmcu_34725", lambda: tcs34725_read(color))
        scd = self.sensors.get("scd41", {})
        if scd.get("enabled"):
            def poll_scd():
                from smbus2 import SMBus, i2c_msg
                if not self._scd_started:
                    with SMBus(int(scd.get("bus", 1))) as bus:
                        bus.i2c_rdwr(i2c_msg.write(int(scd.get("address", 0x62)), [0x21, 0xb1]))
                    self._scd_started = True
                    self._scd_started_at = time.monotonic()
                    return {}
                if time.monotonic() - self._scd_started_at < 5.0:
                    return {}
                try:
                    return scd41_read(scd)
                except OSError:
                    self._scd_started = False
                    raise
            attempt("scd41", poll_scd)

        # The ADC is proven operational when an enabled channel returns a voltage.
        # Its inventory row otherwise remains not-checked instead of faking data.
        if adc.get("enabled") and "soil_voltage_v" in values:
            values["adc_a0_voltage_v"] = values["soil_voltage_v"]
        self.errors = errors
        return values
