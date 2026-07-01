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

    while True:
        try:
            current_time = time.time() - init_time
            Database.time_readings.append(current_time)

            if gui.start_motor_calibration:
                threading.Thread(target=spooler.calibrate, daemon=True).start()
                gui.start_motor_calibration = False

            # Detect Stop transition — always shut down heater on stop
            if device_was_started and not gui.device_started:
                extruder.stop()
                stepper_started = False
                device_was_started = False

            if gui.device_started:
                device_was_started = True
                if not stepper_started:
                    extruder.start_stepper()
                    stepper_started = True
                extruder.temperature_control_loop(current_time)
                if gui.spooling_control_state:
                    spooler.motor_control_loop(current_time)
                fan.control_loop()

            time.sleep(0.05)
        except Exception as e:
            print(f"Error in hardware control loop: {e}")
            gui.show_message("Error in hardware control loop",
                             "Please restart the program.")
            if fan:
                fan.stop()
            if spooler:
                spooler.stop()
            if extruder:
                extruder.stop()


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
