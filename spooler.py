"""File to control the spooling process"""
import time
import numpy as np
import RPi.GPIO as GPIO
from gpiozero import RotaryEncoder

from database import Database
from user_interface import UserInterface


class Spooler:
    """DC Motor Controller for the spooling process"""
    ENCODER_A_PIN = 24
    ENCODER_B_PIN = 23
    PWM_PIN = 5

    PULSES_PER_REVOLUTION = 1176
    READINGS_TO_AVERAGE = 10
    SAMPLE_TIME = 0.1
    DIAMETER_PREFORM = 7
    DIAMETER_SPOOL = 15.2

    def __init__(self, gui: UserInterface) -> None:
        self.gui = gui
        self.encoder = None
        self.pwm = None
        self.slope = Database.get_calibration_data("motor_slope")
        self.intercept = Database.get_calibration_data("motor_intercept")
        self.motor_calibration = True
        if self.slope == -1 or self.intercept == -1:
            self.motor_calibration = False
        GPIO.setup(Spooler.PWM_PIN, GPIO.OUT)
        self.initialize_encoder()
        
        # Control parameters
        self.previous_time = 0.0
        self.integral_diameter = 0.0
        self.previous_error_diameter = 0.0
        self.previous_steps = 0
        self.integral_motor = 0.0
        self.previous_error_motor = 0.0

    def initialize_encoder(self) -> None:
        """Initialize the encoder and SPI"""
        self.encoder = RotaryEncoder(Spooler.ENCODER_A_PIN,
                                     Spooler.ENCODER_B_PIN, max_steps=0)

    def start(self, frequency: float, duty_cycle: float) -> None:
        """Start the DC Motor PWM"""
        self.pwm = GPIO.PWM(Spooler.PWM_PIN, frequency)
        self.pwm.start(duty_cycle)

    def stop(self) -> None:
        """Stop the DC Motor PWM"""
        if self.pwm:
            self.pwm.stop()

    def update_duty_cycle(self, duty_cycle: float) -> None:
        """Update the DC Motor PWM duty cycle"""
        self.pwm.ChangeDutyCycle(duty_cycle)

    def get_average_diameter(self) -> float:
        """Get the average diameter of the fiber, ignoring zero/invalid readings"""
        readings = [d for d in Database.diameter_readings if d > 0]
        if not readings:
            return 0.0
        if len(readings) < Spooler.READINGS_TO_AVERAGE:
            return sum(readings) / len(readings)
        return sum(readings[-Spooler.READINGS_TO_AVERAGE:]) / Spooler.READINGS_TO_AVERAGE

    def diameter_to_rpm(self, diameter: float) -> float:
        """Convert the fiber diameter to RPM of the spooling motor"""
        stepper_rpm = self.gui.extrusion_motor_speed.value()
        return 25/28 * 11 * stepper_rpm * (Spooler.DIAMETER_PREFORM**2 /
                                        (Spooler.DIAMETER_SPOOL * diameter**2))

    def rpm_to_duty_cycle(self, rpm: float) -> float:
        """Convert the RPM to duty cycle"""
        return self.slope * rpm + self.intercept

    def motor_control_loop(self, current_time: float) -> None:
        """Cascaded PID: outer loop controls diameter → RPM setpoint,
        inner loop controls motor speed → duty cycle."""
        if current_time - self.previous_time <= Spooler.SAMPLE_TIME:
            return
        try:
            if not self.motor_calibration:
                self.gui.show_message("Motor calibration data not found",
                                    "Please calibrate the motor.")
                self.motor_calibration = True

            target_diameter = self.gui.target_diameter.value()
            current_diameter = self.get_average_diameter()

            diameter_kp = self.gui.diameter_kp.value()
            diameter_ki = self.gui.diameter_ki.value()
            diameter_kd = self.gui.diameter_kd.value()

            motor_kp = self.gui.motor_kp.value()
            motor_ki = self.gui.motor_ki.value()
            motor_kd = self.gui.motor_kd.value()

            delta_time = current_time - self.previous_time
            self.previous_time = current_time

            # --- Outer PID: diameter error → RPM correction ---
            # Sign: current > target (too thick) → positive error → increase RPM → pull faster → thinner
            error_diameter = current_diameter - target_diameter
            self.integral_diameter += error_diameter * delta_time
            self.integral_diameter = max(min(self.integral_diameter, 0.5), -0.5)
            derivative_diameter = (error_diameter - self.previous_error_diameter) / delta_time
            self.previous_error_diameter = error_diameter
            rpm_correction = (diameter_kp * error_diameter
                              + diameter_ki * self.integral_diameter
                              + diameter_kd * derivative_diameter)

            # Feed-forward from volumetric model + PID correction
            setpoint_rpm = self.diameter_to_rpm(target_diameter) + rpm_correction
            setpoint_rpm = max(min(setpoint_rpm, 60), 0)

            # --- Inner PID: RPM error → duty cycle correction ---
            delta_steps = self.encoder.steps - self.previous_steps
            self.previous_steps = self.encoder.steps
            current_rpm = (delta_steps / Spooler.PULSES_PER_REVOLUTION *
                           60 / delta_time)
            error_motor = setpoint_rpm - current_rpm
            self.integral_motor += error_motor * delta_time
            self.integral_motor = max(min(self.integral_motor, 50), -50)
            derivative_motor = (error_motor - self.previous_error_motor) / delta_time
            self.previous_error_motor = error_motor
            duty_correction = (motor_kp * error_motor
                               + motor_ki * self.integral_motor
                               + motor_kd * derivative_motor)

            # Feed-forward duty from calibration + PID correction
            base_duty = self.rpm_to_duty_cycle(setpoint_rpm)
            output_duty_cycle = base_duty + duty_correction
            output_duty_cycle = max(min(output_duty_cycle, 100), 0)
            self.update_duty_cycle(output_duty_cycle)

            # Update plots
            self.gui.motor_plot.update_plot(current_time, current_rpm, setpoint_rpm)
            self.gui.diameter_plot.update_plot(current_time, current_diameter, target_diameter)

            # Log
            Database.spooler_delta_time.append(delta_time)
            Database.spooler_setpoint.append(setpoint_rpm)
            Database.spooler_rpm.append(current_rpm)
            Database.spooler_duty_cycle.append(output_duty_cycle)
        except Exception as e:
            print(f"Error in motor control loop: {e}")
            self.gui.show_message("Error in motor control loop",
                                  "Please restart the program.")

    def calibrate(self) -> None:
        """Calibrate the DC Motor"""
        rpm_values = []
        duty_cycles = []
        num_samples = 5

        try:
            for duty_cycle in range(20, 101, 10):  # Sweep duty cycle from 0% to 100% in increments of 10%
                rpm_samples = []
                for _ in range(num_samples):
                    self.update_duty_cycle(duty_cycle)
                    time.sleep(2)
                    # Measure RPM
                    oldtime = time.perf_counter()
                    oldpos = self.encoder.steps
                    time.sleep(Spooler.SAMPLE_TIME)
                    newtime = time.perf_counter()
                    newpos = self.encoder.steps
                    dt = newtime - oldtime
                    ds = newpos - oldpos
                    rpm = ds / Spooler.PULSES_PER_REVOLUTION / dt * 60
                    rpm_samples.append(rpm)
                avg_rpm = sum(rpm_samples) / num_samples
                duty_cycles.append(duty_cycle)
                rpm_values.append(avg_rpm)
                print(f"Duty Cycle: {duty_cycle}% -> Avg RPM: {avg_rpm:.2f}")

            # Fit a curve to the data
            coefficients = np.polyfit(rpm_values, duty_cycles, 1)
            self.slope = coefficients[0]
            self.intercept = coefficients[1]
            Database.update_calibration_data("motor_slope", str(self.slope))
            Database.update_calibration_data("motor_intercept", str(self.intercept))

        except KeyboardInterrupt:
            print("\nData collection stopped\n\n")

        self.gui.show_message("Motor calibration completed.",
                               "Please restart the program.")
        self.stop()
        self.previous_steps = self.encoder.steps
