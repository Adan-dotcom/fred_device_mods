"""File to control the extrusion process"""
import time
import math
import threading
import RPi.GPIO as GPIO
import busio
import board
import digitalio
import adafruit_mcp3xxx.mcp3008 as MCP
from adafruit_mcp3xxx.analog_in import AnalogIn

from database import Database
from user_interface import UserInterface

class Thermistor:
    """Constants and util functions for the thermistor"""
    REFERENCE_TEMPERATURE = 298.15 # K
    RESISTANCE_AT_REFERENCE = 100000 # Ω
    BETA_COEFFICIENT = 3977 # K
    VOLTAGE_SUPPLY = 3.3 # V
    RESISTOR = 10000 # Ω
    READINGS_TO_AVERAGE = 10

    @classmethod
    def get_temperature(cls, voltage: float) -> float:
        """Get the average temperature from the voltage using Steinhart-Hart 
        equation"""
        if voltage < 0.0001:  # Prevenir división por cero
            return 0
        resistance = ((cls.VOLTAGE_SUPPLY - voltage) * cls.RESISTOR )/ voltage
        ln = math.log(resistance / cls.RESISTANCE_AT_REFERENCE)
        temperature = (1 / ((ln / cls.BETA_COEFFICIENT) + (1 / cls.REFERENCE_TEMPERATURE))) - 273.15
        Database.temperature_readings.append(temperature)
        average_temperature = 0
        if len(Database.temperature_readings) > cls.READINGS_TO_AVERAGE:
            # Get last constant readings
            average_temperature = (sum(Database.temperature_readings
                                      [-cls.READINGS_TO_AVERAGE:]) /
                                      cls.READINGS_TO_AVERAGE)
        else:
            average_temperature = (sum(Database.temperature_readings) /
                                   len(Database.temperature_readings))
        return average_temperature

class Extruder:
    """Controller of the extrusion process: the heater and stepper motor"""
    HEATER_PIN = 6
    DIRECTION_PIN = 16
    STEP_PIN = 12
    MICROSTEP_PIN_A = 17
    MICROSTEP_PIN_B = 27
    MICROSTEP_PIN_C = 22
    DEFAULT_DIAMETER = 0.35
    MINIMUM_DIAMETER = 0.3
    MAXIMUM_DIAMETER = 0.6
    STEPS_PER_REVOLUTION = 200
    RESOLUTION = {'1': (0, 0, 0),
                  '1/2': (1, 0, 0),
                  '1/4': (0, 1, 0),
                  '1/8': (1, 1, 0),
                 '1/16': (0, 0, 1),
                 '1/32': (1, 0, 1)}
    FACTOR = {'1': 1,
                   '1/2': 2,
                   '1/4': 4,
                   '1/8': 8,
                   '1/16': 16,
                   '1/32': 32}
    DEFAULT_MICROSTEPPING = '1/32'
    SAMPLE_TIME = 0.1
    MAX_OUTPUT = 1
    MIN_OUTPUT = 0
    # Above this reading the thermistor is likely faulty (open circuit reads
    # ~220 C) or the heater is out of range — either way, force the heater off.
    MAX_SAFE_TEMPERATURE = 130

    def __init__(self, gui: UserInterface) -> None:
        self.gui = gui
        self.speed = 0.0
        self.duty_cycle = 0.0
        self.channel_0 = None
        GPIO.setup(Extruder.HEATER_PIN, GPIO.OUT)
        GPIO.setup(Extruder.DIRECTION_PIN, GPIO.OUT)
        GPIO.setup(Extruder.STEP_PIN, GPIO.OUT)
        GPIO.setup(Extruder.MICROSTEP_PIN_A, GPIO.OUT)
        GPIO.setup(Extruder.MICROSTEP_PIN_B, GPIO.OUT)
        GPIO.setup(Extruder.MICROSTEP_PIN_C, GPIO.OUT)

        self.heater_pwm = GPIO.PWM(Extruder.HEATER_PIN, 1000)
        self.heater_pwm.start(0)
        self.motor_step(0)
        self.initialize_thermistor()
        self.set_microstepping(Extruder.DEFAULT_MICROSTEPPING)

        self.current_diameter = 0.0
        self.diameter_setpoint = Extruder.DEFAULT_DIAMETER

        # Control parameters
        self.previous_time = 0.0
        self.previous_error = 0.0
        self.integral = 0.0
        self._first_sample = True
        self._sensor_warning_shown = False

        self._stepper_running = False
        self._stepper_thread = None

    def reset_control(self, current_time: float) -> None:
        """Reset the PID state. Call on every Start so the first delta_time
        is real and no integral windup is carried over from a previous run."""
        self.previous_time = current_time
        self.previous_error = 0.0
        self.integral = 0.0
        self._first_sample = True

    def initialize_thermistor(self):
        """Initialize the SPI for thermistor temperature readings"""
        spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)

        # Create the cs (chip select)
        cs = digitalio.DigitalInOut(board.D8)

        # Create the mcp object
        mcp = MCP.MCP3008(spi, cs)

        # Create analog inputs connected to the input pins on the MCP3008
        self.channel_0 = AnalogIn(mcp, MCP.P0)

    def set_microstepping(self, mode: str) -> None:
        """Set the microstepping mode"""
        GPIO.output(Extruder.MICROSTEP_PIN_A, Extruder.RESOLUTION[mode][0])
        GPIO.output(Extruder.MICROSTEP_PIN_B, Extruder.RESOLUTION[mode][1])
        GPIO.output(Extruder.MICROSTEP_PIN_C, Extruder.RESOLUTION[mode][2])

    def motor_step(self, direction: int) -> None:
        """Step the motor in the given direction"""
        GPIO.output(Extruder.DIRECTION_PIN, direction)

    def start_stepper(self) -> None:
        """Start the stepper motor in a dedicated thread"""
        self._stepper_running = True
        self._stepper_thread = threading.Thread(target=self._stepper_loop,
                                                daemon=True)
        self._stepper_thread.start()

    def stop_stepper(self) -> None:
        """Stop the stepper motor thread"""
        self._stepper_running = False

    def _stepper_loop(self) -> None:
        """Tight loop for stepper — runs in its own thread to avoid timing interference"""
        factor = Extruder.FACTOR[Extruder.DEFAULT_MICROSTEPPING]
        GPIO.output(Extruder.DIRECTION_PIN, 1)
        while self._stepper_running:
            try:
                setpoint_rpm = self.gui.extrusion_motor_speed.value()
                if setpoint_rpm <= 0:
                    time.sleep(0.05)
                    continue
                delay = 60 / setpoint_rpm / Extruder.STEPS_PER_REVOLUTION / factor
                GPIO.output(Extruder.STEP_PIN, GPIO.HIGH)
                time.sleep(delay)
                GPIO.output(Extruder.STEP_PIN, GPIO.LOW)
                time.sleep(delay)
            except Exception as e:
                print(f"Error in stepper loop: {e}")

    def temperature_control_loop(self, current_time: float) -> None:
        """Closed loop control of the temperature of the extruder for desired diameter"""
        if current_time - self.previous_time <= Extruder.SAMPLE_TIME:
            return
        try:
            target_temperature = self.gui.target_temperature.value()
            kp = self.gui.temperature_kp.value()
            ki = self.gui.temperature_ki.value()
            kd = self.gui.temperature_kd.value()

            delta_time = current_time - self.previous_time
            self.previous_time = current_time
            voltage = self.channel_0.voltage
            temperature = Thermistor.get_temperature(voltage)

            if self._first_sample:
                # First tick only starts the clock — delta_time from a stale
                # previous_time would blow up the integral and derivative.
                self._first_sample = False
                return

            # Sensor sanity: an open/disconnected thermistor pulls the ADC
            # near the supply rail and reads as a very high temperature.
            if temperature > Extruder.MAX_SAFE_TEMPERATURE:
                self.heater_pwm.ChangeDutyCycle(0)
                Database.update("heater_duty_pct", 0.0)
                if not self._sensor_warning_shown:
                    self._sensor_warning_shown = True
                    print(f"WARNING: temperature reads {temperature:.0f} C "
                          f"(thermistor voltage {voltage:.2f} V). Heater forced "
                          "off. If the heater is cold, check the thermistor "
                          "wiring — a reading near 220 C with ~3.2 V means the "
                          "thermistor branch is open/disconnected.")
                return

            error = target_temperature - temperature
            self.integral += error * delta_time
            derivative = (error - self.previous_error) / delta_time
            self.previous_error = error
            output = kp * error + ki * self.integral + kd * derivative
            if output > Extruder.MAX_OUTPUT:
                output = Extruder.MAX_OUTPUT
            elif output < Extruder.MIN_OUTPUT:
                output = Extruder.MIN_OUTPUT
            self.heater_pwm.ChangeDutyCycle(output * 100)
            self.gui.temperature_plot.update_plot(current_time, temperature,
                                                    target_temperature)
            Database.update("temperature_c", temperature)
            Database.update("temperature_setpoint_c", float(target_temperature))
            Database.update("thermistor_v", voltage)
            Database.update("heater_duty_pct", output * 100)
            # Stepper reference (open loop — there is no measured stepper speed)
            Database.update("extruder_setpoint_rpm",
                            self.gui.extrusion_motor_speed.value())
        except Exception as e:
            print(f"Error in temperature control loop: {e}")
            self.gui.show_message("Error in temperature control loop",
                                  "Please restart the program.")

    def stop(self) -> None:
        """Stop the heater and stepper. The PWM object is kept alive (duty 0)
        so the heater works again on the next Start without a restart."""
        self.stop_stepper()
        self.heater_pwm.ChangeDutyCycle(0)
        Database.update("heater_duty_pct", 0.0)

    def shutdown(self) -> None:
        """Full stop — only on program exit"""
        self.stop()
        self.heater_pwm.stop()

