"""Optional local single-button/LED controller.

Input is active-low with a pull-up. First short press displays status;
second short press starts in READY; 3-second hold stops in MEASURING.
No state indication is shown until requested; display times out in 10 s.
"""
import asyncio
import logging
import time
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


def load_hardware(path):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    button = data.get("button", {})
    led = data.get("led", {})
    if not isinstance(button, dict) or not isinstance(led, dict):
        raise ValueError("button and led must be mappings")
    for device in (button, led):
        if device.get("enabled", False):
            pin = device.get("gpio")
            if type(pin) is not int or pin not in range(2, 28):
                raise ValueError("GPIO must be a valid BCM GPIO 2..27")
    relay = data.get("relays", {}).get("relay_2", {})
    if relay.get("enabled") and (relay.get("gpio") != 20 or relay.get("laser_off_level") is not True):
        raise ValueError("Relay 2 requires verified GPIO20 and active HIGH laser-OFF polarity")
    if button.get("enabled") and led.get("enabled") and button["gpio"] == led["gpio"]:
        raise ValueError("Button and LED cannot share a GPIO")
    if not 0.01 <= float(button.get("debounce_ms", 50)) / 1000 <= 0.5:
        raise ValueError("Invalid debounce interval")
    if not 1 <= float(button.get("long_press_seconds", 3)) <= 15:
        raise ValueError("Invalid long-press duration")
    if not 2 <= float(led.get("status_timeout_seconds", 10)) <= 120:
        raise ValueError("Invalid status timeout")
    return data


class ButtonLogic:
    """Pure state machine; easily testable without GPIO hardware."""
    def __init__(self, engine, timeout=10, long_press=3):
        self.engine = engine
        self.timeout = timeout
        self.long_press = long_press
        self.visible_until = 0.0
        self.pressed_at = None
        self.stop_triggered = False
        self.await_release = True  # Ignore buttons held during boot
        self.armed = False

    def update(self, pressed, now):
        if self.await_release:
            if not pressed:
                self.await_release = False
            return
        if not pressed and self.pressed_at is None:
            if now >= self.visible_until:
                self.armed = False
            return
        if pressed and self.pressed_at is None:
            self.pressed_at = now
            self.stop_triggered = False
            return
        if pressed and self.pressed_at is not None:
            if (not self.stop_triggered and self.armed and
                now < self.visible_until and now-self.pressed_at >= self.long_press and
                self.engine.status()["state"] == "MEASURING"):
                self.engine.stop()
                self.stop_triggered = True
                self.visible_until = now+self.timeout
            return
        if self.pressed_at is not None:
            duration = now-self.pressed_at
            self.pressed_at = None
            if self.stop_triggered:
                self.armed = False
                return
            if duration >= self.long_press:
                return  # never turn a long hold into a start
            if not self.armed or now >= self.visible_until:
                self.armed = True
                self.visible_until = now+self.timeout
            elif self.engine.status()["state"] == "READY":
                self.engine.start()
                self.armed = False
                self.visible_until = now+self.timeout
            else:
                self.armed = False
                self.visible_until = now+self.timeout

    def output(self, now):
        if now >= self.visible_until:
            return False
        state = self.engine.status()["state"]
        if state == "MEASURING":
            return True
        t = now % 2.0
        if state == "READY":
            return t < 0.5  # 0.5 Hz
        if state == "STOPPED":
            t = now % 2.0
            return (0 <= t < 0.14) or (0.28 <= t < 0.42)
        if state == "ERROR":
            return (now % .25) < .125
        return False  # FINISHED, BOOT, SHUTDOWN: no indication


class HardwareController:
    def __init__(self, engine, config):
        self.engine = engine
        self.config = config
        self.button = None
        self.led = None
        self.laser_relay = None

    async def run(self):
        btn_cfg = self.config.get("button", {})
        led_cfg = self.config.get("led", {})
        relay_cfg = self.config.get("relays", {}).get("relay_2", {})
        if not (btn_cfg.get("enabled") or led_cfg.get("enabled") or relay_cfg.get("enabled")):
            return
        try:
            from gpiozero import Button, LED
        except ImportError as exc:
            raise RuntimeError("Install gpiozero to enable physical button/LED") from exc
        try:
            if relay_cfg.get("enabled"):
                from gpiozero import OutputDevice
                # Assert OFF before any laser measurement is permitted.
                self.laser_relay = OutputDevice(relay_cfg["gpio"], active_high=True, initial_value=True)
            if btn_cfg.get("enabled"):
                self.button = Button(btn_cfg["gpio"], pull_up=btn_cfg.get("pull_up", True),
                                     bounce_time=float(btn_cfg.get("debounce_ms", 50))/1000)
            if led_cfg.get("enabled"):
                self.led = LED(led_cfg["gpio"], active_high=led_cfg.get("active_high", True),
                               initial_value=False)
            logic = ButtonLogic(self.engine, float(led_cfg.get("status_timeout_seconds", 10)),
                                float(btn_cfg.get("long_press_seconds", 3)))
            while True:
                now = time.monotonic()
                try:
                    logic.update(bool(self.button.is_pressed) if self.button else False, now)
                    if self.led:
                        self.led.on() if logic.output(now) else self.led.off()
                except Exception:
                    log.exception("Button/LED handling failed")
                if self.laser_relay:
                    # Laser is enabled only while an experiment is MEASURING.
                    self.laser_relay.off() if self.engine.status()["state"] == "MEASURING" else self.laser_relay.on()
                await asyncio.sleep(0.05)
        finally:
            if self.laser_relay:
                self.laser_relay.on()  # safest possible state during orderly shutdown
                self.laser_relay.close()
            if self.led:
                self.led.off()
                self.led.close()
            if self.button:
                self.button.close()
