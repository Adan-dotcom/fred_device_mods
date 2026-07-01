"""Main file to run the FrED device"""
import threading
import time
import RPi.GPIO as GPIO
from database import Database
from user_interface import UserInterface
from fan import Fan
from spooler import Spooler
from extruder import Extruder


def hardware_control(gui: UserInterface) -> None:
    """Thread to handle hardware control"""
    time.sleep(1)
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    fan = None
    spooler = None
    extruder = None
    try:
        fan = Fan(gui)
        spooler = Spooler(gui)
        extruder = Extruder(gui)
        fan.start(1000, 45)
        spooler.start(1000, 0)
    except Exception as e:
        print(f"Error initializing hardware: {e}")
        gui.show_message("Error while starting the device",
                         "Please restart the program.")
        return

    init_time = time.time()
    stepper_started = False
    device_was_started = False
    spooling_was_on = False
    last_log_time = 0.0
    LOG_PERIOD = 0.1  # one CSV row every 100 ms (10 Hz)

    while True:
        try:
            current_time = time.time() - init_time

            if gui.start_motor_calibration:
                threading.Thread(target=spooler.calibrate, daemon=True).start()
                gui.start_motor_calibration = False

            # Detect Stop transition — shut down heater AND spooler motor
            if device_was_started and not gui.device_started:
                extruder.stop()
                spooler.update_duty_cycle(0)
                Database.update("spooler_duty_pct", 0.0)
                stepper_started = False
                device_was_started = False
                spooling_was_on = False
                Database.end_session()

            if gui.device_started:
                if not device_was_started:
                    # Start transition: fresh PID state and a new log file
                    device_was_started = True
                    extruder.reset_control(current_time)
                    spooler.reset_control(current_time)
                    Database.start_session()
                if not stepper_started:
                    extruder.start_stepper()
                    stepper_started = True
                extruder.temperature_control_loop(current_time)
                if gui.spooling_control_state:
                    spooling_was_on = True
                    spooler.motor_control_loop(current_time)
                else:
                    if spooling_was_on:
                        # Closed loop switched off — do not leave the motor
                        # running at the last commanded duty cycle
                        spooling_was_on = False
                        spooler.update_duty_cycle(0)
                        Database.update("spooler_duty_pct", 0.0)
                        Database.update("spooler_setpoint_rpm", 0.0)
                    # Keep measuring/plotting motor RPM even in open loop
                    spooler.update_rpm_display(current_time)
                fan.control_loop()

                if current_time - last_log_time >= LOG_PERIOD:
                    last_log_time = current_time
                    Database.log_row(current_time)

            time.sleep(0.05)
        except Exception as e:
            print(f"Error in hardware control loop: {e}")
            # Stop the device so the loop does not retry forever and spam
            # error dialogs; the user can press Start to try again.
            gui.device_started = False
            gui.spooling_control_state = False
            stepper_started = False
            device_was_started = False
            spooling_was_on = False
            # Zero the outputs but keep the PWM objects alive so a retry
            # actually works. The fan is left running to keep cooling a
            # possibly hot heater block.
            try:
                if extruder:
                    extruder.stop()
            except Exception as stop_error:
                print(f"Error stopping extruder: {stop_error}")
            try:
                if spooler:
                    spooler.update_duty_cycle(0)
            except Exception as stop_error:
                print(f"Error stopping spooler: {stop_error}")
            Database.end_session()
            gui.show_message("Error in hardware control loop",
                             "Device stopped. Press Start to try again.")


if __name__ == "__main__":
    print("Starting FrED Device...")
    ui = UserInterface()
    time.sleep(2)
    hardware_thread = threading.Thread(target=hardware_control, args=(ui,),
                                       daemon=True)
    hardware_thread.start()
    ui.start_gui()
    # hardware_thread is daemon — killed automatically when main thread exits
    print("FrED Device Closed.")
