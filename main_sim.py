"""Run the full FrED application with simulated hardware (no RPi needed).

    python main_sim.py          Interactive GUI with a real-time simulated
                                plant — click the same buttons as on the RPi.
    python main_sim.py --auto   Self-test: starts the device and the spooling
                                loop, runs, stops, then validates the CSV log.
                                Exit code 0 = pass, 1 = fail.

Never run this on the RPi — use main.py there.
"""
import sys
import time
import csv
import threading

import sim_hardware

AUTO = "--auto" in sys.argv
if AUTO:
    sim_hardware.SimState.TIME_SCALE = 15.0  # accelerate the thermal plant

state = sim_hardware.install()

# FrED modules must be imported AFTER install()
from PyQt5.QtCore import QMetaObject, Qt  # noqa: E402
from database import Database, CSV_HEADER  # noqa: E402
from user_interface import UserInterface  # noqa: E402
import main  # noqa: E402  (only for hardware_control — __main__ guard keeps it inert)


def auto_driver(gui: UserInterface, results: dict) -> None:
    """Drives the GUI flags like a user would press the buttons."""
    time.sleep(3)  # let the hardware thread initialize
    print("[auto] Start device")
    gui.device_started = True
    time.sleep(4)
    print("[auto] Enable spooling closed loop")
    gui.spooling_control_state = True
    time.sleep(10)
    print("[auto] Disable spooling closed loop (motor must stop)")
    gui.spooling_control_state = False
    time.sleep(3)
    results["motor_duty_after_toggle_off"] = state.motor_duty
    results["device_survived"] = gui.device_started
    print("[auto] Stop device")
    gui.device_started = False
    time.sleep(1.5)
    results["heater_pin_after_stop"] = state.pin_levels.get(sim_hardware.HEATER_PIN)
    results["motor_duty_after_stop"] = state.motor_duty
    results["log_path"] = Database._session_path
    QMetaObject.invokeMethod(gui.app, "quit", Qt.QueuedConnection)


def validate(results: dict) -> int:
    failures = []

    def check(name, condition, detail=""):
        print(f"  {'OK  ' if condition else 'FAIL'} {name} {detail if not condition else ''}")
        if not condition:
            failures.append(name)

    print("[auto] Validating run")
    check("device stayed running (no hardware-loop error)",
          results.get("device_survived") is True)
    check("motor stopped when closed loop toggled off",
          results.get("motor_duty_after_toggle_off") == 0)
    check("heater pin LOW after stop",
          results.get("heater_pin_after_stop") == 0)
    check("motor duty 0 after stop", results.get("motor_duty_after_stop") == 0)

    log_path = results.get("log_path")
    check("log file exists", log_path is not None and log_path.exists())
    if log_path is None or not log_path.exists():
        return 1

    with open(log_path, encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    check("log has data rows", len(rows) > 50, f"got {len(rows)}")

    def column(name):
        return [float(r[name]) for r in rows if r[name] not in ("", None)]

    timestamps = column("timestamp")
    check("timestamps monotonic",
          all(b >= a for a, b in zip(timestamps, timestamps[1:])))
    check("timestamps are real unix time", abs(timestamps[0] - time.time()) < 300)
    span = timestamps[-1] - timestamps[0]
    rate = (len(rows) - 1) / span if span > 0 else 0
    check("logging rate sane (4-11 Hz)", 4 <= rate <= 11,
          f"got {rate:.1f} Hz")

    temps = column("temperature_c")
    check("temperature logged", len(temps) > 50, f"got {len(temps)}")
    check("plant heated up (max temp > 45 C)", temps and max(temps) > 45,
          f"max {max(temps):.1f}" if temps else "none")
    heater = column("heater_duty_pct")
    check("heater was driven (duty > 50% seen)", heater and max(heater) > 50)

    rpm = column("spooler_rpm")
    check("motor spun (rpm > 5 seen)", rpm and max(rpm) > 5,
          f"max {max(rpm):.1f}" if rpm else "none")
    duty = column("spooler_duty_pct")
    check("spooler duty logged", len(duty) > 0)
    check("closed loop raised duty", duty and max(duty) > 20)

    diameters = column("diameter_mm")
    check("camera pipeline produced diameter readings", len(diameters) > 5,
          f"got {len(diameters)}")
    volts = column("thermistor_v")
    check("thermistor voltage logged and sane",
          volts and all(0.0 < v < 3.3 for v in volts))

    print(f"[auto] max temp {max(temps):.1f} C | max rpm {max(rpm):.1f} | "
          f"rows {len(rows)}")
    if failures:
        print(f"[auto] {len(failures)} FAILURES: {failures}")
        return 1
    print("[auto] ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    print(f"Starting FrED Device (SIMULATION{', auto-test' if AUTO else ''})...")
    ui = UserInterface()

    if AUTO:
        # Dialogs would block the auto run — print them instead
        ui._messenger.message.disconnect()
        ui._messenger.message.connect(
            lambda title, message: print(f"[dialog] {title}: {message}"))

    time.sleep(1)
    hardware_thread = threading.Thread(target=main.hardware_control,
                                       args=(ui,), daemon=True)
    hardware_thread.start()

    results = {}
    if AUTO:
        threading.Thread(target=auto_driver, args=(ui, results),
                         daemon=True).start()

    ui.start_gui()

    if AUTO:
        sys.exit(validate(results))
    print("FrED Device (simulation) closed.")
