"""Hardware simulation so the full FrED app runs on a dev machine (no RPi).

Call sim_hardware.install() BEFORE importing any FrED module. It registers
fake RPi.GPIO / gpiozero / busio / board / digitalio / adafruit modules in
sys.modules and replaces cv2.VideoCapture with a player that cycles the
sample fiber images committed in the repo.

The mocks are wired to a small physics model (SimState) so the closed loops
actually respond: driving the heater pin heats a first-order thermal plant
read back through the simulated thermistor divider; driving the DC motor PWM
spins a first-order motor read back through the simulated encoder.

This file is never imported on the RPi — zero effect on real hardware.
"""
import sys
import time
import math
import types
import threading
from pathlib import Path

REPO_DIR = Path(__file__).parent

HEATER_PIN = 6
DC_MOTOR_PIN = 5


class SimState:
    """Physics model shared by all the mocks"""
    TIME_SCALE = 1.0  # >1 accelerates the thermal plant (used by auto-tests)

    AMBIENT_C = 25.0
    HEATER_GAIN_C = 130.0   # steady-state temperature rise at 100% duty
    THERMAL_TAU_S = 120.0
    # Matches calibration.yaml so the feed-forward in spooler.py is exact
    MOTOR_SLOPE = 2.3535585694303376     # duty = slope * rpm + intercept
    MOTOR_INTERCEPT = -38.74085878574971
    MOTOR_DEADBAND_DUTY = 10.0           # below this duty the motor stalls
    MOTOR_TAU_S = 0.3
    PULSES_PER_REVOLUTION = 1176
    # Thermistor divider constants (match extruder.Thermistor)
    T0_K = 298.15
    R0 = 100000.0
    BETA = 3977.0
    VCC = 3.3
    R_FIXED = 10000.0

    def __init__(self) -> None:
        self.lock = threading.RLock()
        now = time.time()
        self.pin_levels = {}
        # Heater: track the exact fraction of time the pin was HIGH between
        # thermistor reads, so the 20 Hz time-proportional drive is not
        # aliased by the 10 Hz sampling.
        self.temperature = self.AMBIENT_C
        self._temp_updated = now
        self._heater_on = False
        self._heater_last_change = now
        self._heater_on_accum = 0.0
        # DC motor
        self.motor_duty = 0.0
        self.motor_rpm = 0.0
        self._motor_updated = now
        self._encoder_steps = 0.0

    # ------------------------- heater / thermistor -------------------------
    def set_pin(self, pin: int, level) -> None:
        with self.lock:
            self.pin_levels[pin] = level
            if pin == HEATER_PIN:
                now = time.time()
                if self._heater_on:
                    self._heater_on_accum += now - self._heater_last_change
                self._heater_on = bool(level)
                self._heater_last_change = now

    def _heater_duty_since_last_update(self, now: float, dt: float) -> float:
        on_time = self._heater_on_accum
        if self._heater_on:
            on_time += now - self._heater_last_change
        self._heater_on_accum = 0.0
        self._heater_last_change = now
        if dt <= 0:
            return 1.0 if self._heater_on else 0.0
        return min(1.0, on_time / dt)

    def update_temperature(self) -> float:
        with self.lock:
            now = time.time()
            dt = now - self._temp_updated
            if dt <= 0:
                return self.temperature
            duty = self._heater_duty_since_last_update(now, dt)
            self._temp_updated = now
            target = self.AMBIENT_C + self.HEATER_GAIN_C * duty
            alpha = 1.0 - math.exp(-dt * self.TIME_SCALE / self.THERMAL_TAU_S)
            self.temperature += (target - self.temperature) * alpha
            return self.temperature

    def thermistor_voltage(self) -> float:
        temp_k = self.update_temperature() + 273.15
        resistance = self.R0 * math.exp(self.BETA * (1.0 / temp_k - 1.0 / self.T0_K))
        return self.VCC * self.R_FIXED / (self.R_FIXED + resistance)

    # ------------------------------ DC motor -------------------------------
    def set_motor_duty(self, duty: float) -> None:
        with self.lock:
            self._update_motor()
            self.motor_duty = duty

    def _update_motor(self) -> None:
        now = time.time()
        dt = now - self._motor_updated
        if dt <= 0:
            return
        self._motor_updated = now
        if self.motor_duty < self.MOTOR_DEADBAND_DUTY:
            rpm_ss = 0.0
        else:
            rpm_ss = max(0.0, (self.motor_duty - self.MOTOR_INTERCEPT)
                         / self.MOTOR_SLOPE)
        alpha = 1.0 - math.exp(-dt / self.MOTOR_TAU_S)
        # advance the encoder with the average rpm over the interval
        avg_rpm = self.motor_rpm + (rpm_ss - self.motor_rpm) * alpha / 2.0
        self._encoder_steps += avg_rpm / 60.0 * self.PULSES_PER_REVOLUTION * dt
        self.motor_rpm += (rpm_ss - self.motor_rpm) * alpha

    @property
    def encoder_steps(self) -> int:
        with self.lock:
            self._update_motor()
            return int(self._encoder_steps)


STATE = SimState()


# ------------------------------ fake modules -------------------------------
def _build_gpio_module(state: SimState) -> types.ModuleType:
    gpio = types.ModuleType("RPi.GPIO")
    gpio.BCM, gpio.BOARD = 11, 10
    gpio.OUT, gpio.IN = 0, 1
    gpio.HIGH, gpio.LOW = 1, 0
    gpio.setmode = lambda mode: None
    gpio.setwarnings = lambda flag: None
    gpio.setup = lambda pin, mode: None
    gpio.output = lambda pin, level: state.set_pin(pin, level)
    gpio.cleanup = lambda *args: None

    class PWM:
        def __init__(self, pin: int, frequency: float) -> None:
            self.pin = pin

        def _apply(self, duty: float) -> None:
            if self.pin == DC_MOTOR_PIN:
                state.set_motor_duty(duty)

        def start(self, duty: float) -> None:
            self._apply(duty)

        def stop(self) -> None:
            self._apply(0.0)

        def ChangeDutyCycle(self, duty: float) -> None:
            self._apply(duty)

    gpio.PWM = PWM
    return gpio


def _build_gpiozero_module(state: SimState) -> types.ModuleType:
    gpiozero = types.ModuleType("gpiozero")

    class RotaryEncoder:
        def __init__(self, *args, **kwargs) -> None:
            pass

        @property
        def steps(self) -> int:
            return state.encoder_steps

    gpiozero.RotaryEncoder = RotaryEncoder
    return gpiozero


def _build_adafruit_modules(state: SimState):
    mcp3008 = types.ModuleType("adafruit_mcp3xxx.mcp3008")

    class MCP3008:
        def __init__(self, spi, cs) -> None:
            pass

    mcp3008.MCP3008 = MCP3008
    for i in range(8):
        setattr(mcp3008, f"P{i}", i)

    analog_in = types.ModuleType("adafruit_mcp3xxx.analog_in")

    class AnalogIn:
        def __init__(self, mcp, pin) -> None:
            pass

        @property
        def voltage(self) -> float:
            return state.thermistor_voltage()

    analog_in.AnalogIn = AnalogIn

    package = types.ModuleType("adafruit_mcp3xxx")
    package.mcp3008 = mcp3008
    package.analog_in = analog_in
    return package, mcp3008, analog_in


def _build_board_stack():
    busio = types.ModuleType("busio")
    busio.SPI = lambda **kwargs: object()

    board = types.ModuleType("board")
    board.SCK = board.MISO = board.MOSI = board.D8 = object()

    digitalio = types.ModuleType("digitalio")
    digitalio.DigitalInOut = lambda pin: object()
    return busio, board, digitalio


class FakeVideoCapture:
    """Serves the sample fiber .jpg images from the repo in a loop"""

    def __init__(self, index) -> None:
        import cv2
        self.frames = [cv2.imread(str(path))
                       for path in sorted(REPO_DIR.glob("*.jpg"))]
        self.frames = [f for f in self.frames if f is not None]
        self.index = 0

    def isOpened(self) -> bool:
        return bool(self.frames)

    def read(self):
        if not self.frames:
            return False, None
        frame = self.frames[self.index % len(self.frames)]
        self.index += 1
        return True, frame.copy()

    def release(self) -> None:
        self.frames = []


def install() -> SimState:
    """Register all mocks. Must run before importing any FrED module."""
    gpio = _build_gpio_module(STATE)
    rpi_package = types.ModuleType("RPi")
    rpi_package.GPIO = gpio
    sys.modules["RPi"] = rpi_package
    sys.modules["RPi.GPIO"] = gpio

    sys.modules["gpiozero"] = _build_gpiozero_module(STATE)

    package, mcp3008, analog_in = _build_adafruit_modules(STATE)
    sys.modules["adafruit_mcp3xxx"] = package
    sys.modules["adafruit_mcp3xxx.mcp3008"] = mcp3008
    sys.modules["adafruit_mcp3xxx.analog_in"] = analog_in

    busio, board, digitalio = _build_board_stack()
    sys.modules["busio"] = busio
    sys.modules["board"] = board
    sys.modules["digitalio"] = digitalio

    import cv2
    cv2.VideoCapture = FakeVideoCapture

    print("sim_hardware: mocks installed (simulated plant, encoder, camera)")
    return STATE
