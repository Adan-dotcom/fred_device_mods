"""Data logging: streams samples to a CSV file on disk (one table, one row
per sample tick, real timestamps) and keeps small rolling buffers in RAM for
the control loops."""
import csv
import time
import shutil
from pathlib import Path
from collections import deque

import yaml

CALIBRATION_FILE = Path(__file__).parent / "calibration.yaml"

# Single-table CSV format: one row per control tick (sample-and-hold for
# signals that update slower than the logging rate, e.g. the camera).
CSV_HEADER = [
    "timestamp",              # real unix time of the row (time.time())
    "elapsed_s",              # seconds since device start
    "temperature_c",          # averaged thermistor temperature
    "temperature_setpoint_c",
    "thermistor_v",           # raw ADC voltage (sensor diagnostics / observer)
    "heater_duty_pct",        # duty actually sent to the heater PWM
    "spooler_rpm",            # measured from encoder
    "spooler_setpoint_rpm",
    "spooler_duty_pct",       # duty actually sent to the DC motor PWM
    "encoder_steps",          # raw cumulative encoder count (diagnostics)
    "extruder_setpoint_rpm",  # stepper reference (open loop, no measurement)
    "diameter_mm",            # last camera measurement
    "diameter_setpoint_mm",
    "fan_duty_pct",
]


class Database():
    """Rolling buffers for control + streaming CSV logger"""
    # Rolling buffers used by the control loops (bounded, no memory growth)
    temperature_readings = deque(maxlen=100)
    diameter_readings = deque(maxlen=50)

    # Latest value of each signal, written out on every log_row() call
    current = {field: "" for field in CSV_HEADER}

    _file = None
    _writer = None
    _session_path = None
    _last_flush = 0.0

    @classmethod
    def update(cls, field: str, value) -> None:
        """Update the latest value of a signal (sample-and-hold)"""
        cls.current[field] = value

    @classmethod
    def start_session(cls) -> None:
        """Open a new timestamped CSV file and start streaming to it"""
        if cls._file is not None:
            return
        name = time.strftime("fred_log_%Y%m%d_%H%M%S.csv")
        cls._session_path = Path(__file__).parent / name
        cls._file = open(cls._session_path, mode="w", newline="",
                         encoding="utf-8")
        cls._writer = csv.writer(cls._file)
        cls._writer.writerow(CSV_HEADER)
        cls._last_flush = time.time()
        print(f"Logging to {cls._session_path}")

    @classmethod
    def log_row(cls, elapsed: float) -> None:
        """Write one row with the current value of every signal.
        Buffered I/O, flushed at most once per second to limit SD wear."""
        if cls._writer is None:
            return
        cls.current["timestamp"] = f"{time.time():.3f}"
        cls.current["elapsed_s"] = f"{elapsed:.3f}"
        cls._writer.writerow(
            [f"{v:.4f}" if isinstance(v, float) else v
             for v in (cls.current[field] for field in CSV_HEADER)])
        now = time.time()
        if now - cls._last_flush >= 1.0:
            cls._file.flush()
            cls._last_flush = now

    @classmethod
    def end_session(cls) -> None:
        """Close the current CSV file"""
        if cls._file is None:
            return
        cls._file.flush()
        cls._file.close()
        cls._file = None
        cls._writer = None
        print(f"Log saved to {cls._session_path}")

    @classmethod
    def export_csv(cls, filename: str) -> str:
        """Copy the session log to the user-given filename.
        Returns the path of the copy, or an empty string if no data."""
        if cls._session_path is None or not cls._session_path.exists():
            return ""
        if cls._file is not None:
            cls._file.flush()
        destination = Path(__file__).parent / (filename + ".csv")
        shutil.copyfile(cls._session_path, destination)
        print(f"CSV file {destination} generated.")
        return str(destination)

    @staticmethod
    def get_calibration_data(field: str) -> float:
        """Get calibration data from the yaml file"""
        try:
            with open(CALIBRATION_FILE, "r", encoding="utf-8") as file:
                calibration_data = yaml.unsafe_load(file)
            return calibration_data[field]
        except (FileNotFoundError, KeyError):
            return -1

    @staticmethod
    def update_calibration_data(field: str, value: str) -> None:
        """Update calibration data in the yaml file"""
        try:
            with open(CALIBRATION_FILE, "r") as file:
                calibration_data = yaml.unsafe_load(file) or {}
        except FileNotFoundError:
            calibration_data = {}
        with open(CALIBRATION_FILE, "w") as file:
            calibration_data[field] = float(value)
            yaml.dump(calibration_data, file)
