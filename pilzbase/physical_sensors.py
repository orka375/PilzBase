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
        r = raw * float(cfg.get("reference_ohms", 430)) / 32768
        nominal = float(cfg.get("rtd_nominal_ohms", 100))
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
            raise IOError("ENS160 data not ready / warming up / invalid")
        aqi = bus.read_byte_data(addr, 0x21)
        tvoc = bus.read_word_data(addr, 0x22)
        eco2 = bus.read_word_data(addr, 0x24)
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
    """CJMCU-34725/TCS34725: raw clear, red, green and blue counts.

    The color sensor does not measure UV. Readings depend on integration time,
    gain and illumination; these are not calibrated lux or UV values.
    """
    from smbus import SMBus
    bus_id = int(cfg.get("bus", 1))
    addr = int(cfg.get("address", 0x29))
    with closing(SMBus(bus_id)) as bus:
        chip_id = bus.read_byte_data(addr, 0x80 | 0x12)
        if chip_id not in (0x44, 0x4d):
            raise IOError(f"Unexpected TCS34725 chip ID 0x{chip_id:02x}")
        # Integration ~50.4 ms (ATIME 0xEB), gain 4x.
        bus.write_byte_data(addr, 0x80 | 0x01, 0xeb)
        bus.write_byte_data(addr, 0x80 | 0x0f, 0x01)
        bus.write_byte_data(addr, 0x80 | 0x00, 0x01)  # oscillator on
        time.sleep(0.003)
        bus.write_byte_data(addr, 0x80 | 0x00, 0x03)  # RGBC ADC on
        time.sleep(0.065)
        status = bus.read_byte_data(addr, 0x80 | 0x13)
        if not status & 0x01:
            raise IOError("TCS34725 RGBC sample not ready")
        data = bus.read_i2c_block_data(addr, 0x80 | 0x20 | 0x14, 8)
    words = [(data[i] | (data[i+1] << 8)) for i in (0, 2, 4, 6)]
    return dict(zip(("color_clear_raw", "color_red_raw", "color_green_raw", "color_blue_raw"), words))


class PhysicalSensors:
    def __init__(self, config):
        self.cfg = config
        self.sensors = config.get("sensors", {})
        self.errors = {}
        self._scd_started = False

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
                    return {}
                return scd41_read(scd)
            attempt("scd41", poll_scd)

        self.errors = errors
        return values
