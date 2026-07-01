import yaml
import csv
from pathlib import Path

CALIBRATION_FILE = Path(__file__).parent / "calibration.yaml"


class Database():
    """Class to store the raw data and generate the CSV file"""
    time_readings = []

    temperature_delta_time = []
    temperature_readings = []
    temperature_setpoint = []
    temperature_error = []
    temperature_pid_output = []
    temperature_kp = []
    temperature_ki = []
    temperature_kd = []
    extruder_rpm = []

    diameter_delta_time = []
    diameter_readings = []
    diameter_setpoint = []

    spooler_delta_time = []
    spooler_setpoint = []
    spooler_rpm = []
    spooler_duty_cycle = []

    fan_duty_cycle = []

    vision_left_edge = []
    vision_right_edge = []

    @classmethod
    def generate_csv(cls, filename: str) -> None:
        """Generate a CSV file with the data"""
        filename = filename + ".csv"
        with open(filename, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)

            total_time = cls.time_readings[-1] if cls.time_readings else 0

            # Temperature Table
            writer.writerow(["TEMPERATURE DATA"])
            writer.writerow(["Elapsed Time (s)", "Temperature (C)",
                            "Temperature setpoint (C)", "Temperature error (C)",
                            "Temperature PID output", "Temperature Kp",
                            "Temperature Ki", "Temperature Kd"])

            temp_samples = len([x for x in cls.temperature_readings if x != ""])
            if temp_samples > 0:
                time_interval = total_time / (temp_samples - 1) if temp_samples > 1 else 0
                for i in range(temp_samples):
                    current_time = i * time_interval
                    writer.writerow([
                        f"{current_time:.3f}",
                        cls.temperature_readings[i] if i < len(cls.temperature_readings) else "",
                        cls.temperature_setpoint[i] if i < len(cls.temperature_setpoint) else "",
                        cls.temperature_error[i] if i < len(cls.temperature_error) else "",
                        cls.temperature_pid_output[i] if i < len(cls.temperature_pid_output) else "",
                        cls.temperature_kp[i] if i < len(cls.temperature_kp) else "",
                        cls.temperature_ki[i] if i < len(cls.temperature_ki) else "",
                        cls.temperature_kd[i] if i < len(cls.temperature_kd) else ""])

            writer.writerow([])
            writer.writerow([])

            # Diameter Table
            writer.writerow(["DIAMETER DATA"])
            writer.writerow(["Elapsed Time (s)", "Diameter (mm)",
                            "Diameter setpoint (mm)", "Fan duty cycle (%)",
                            "Left edge (px)", "Right edge (px)"])

            diameter_samples = len([x for x in cls.diameter_readings if x != ""])
            if diameter_samples > 0:
                time_interval = total_time / (diameter_samples - 1) if diameter_samples > 1 else 0
                for i in range(diameter_samples):
                    current_time = i * time_interval
                    writer.writerow([
                        f"{current_time:.3f}",
                        cls.diameter_readings[i] if i < len(cls.diameter_readings) else "",
                        cls.diameter_setpoint[i] if i < len(cls.diameter_setpoint) else "",
                        cls.fan_duty_cycle[i] if i < len(cls.fan_duty_cycle) else "0",
                        cls.vision_left_edge[i] if i < len(cls.vision_left_edge) else "",
                        cls.vision_right_edge[i] if i < len(cls.vision_right_edge) else ""])

            writer.writerow([])
            writer.writerow([])

            # Motor Table
            writer.writerow(["MOTOR DATA"])
            writer.writerow(["Elapsed Time (s)", "Extruder RPM",
                            "Spooler setpoint (RPM)", "Spooler RPM",
                            "Spooler duty cycle (%)"])

            motor_samples = len([x for x in cls.spooler_rpm if x != ""])
            if motor_samples > 0:
                time_interval = total_time / (motor_samples - 1) if motor_samples > 1 else 0
                for i in range(motor_samples):
                    current_time = i * time_interval
                    writer.writerow([
                        f"{current_time:.3f}",
                        cls.extruder_rpm[i] if i < len(cls.extruder_rpm) else "",
                        cls.spooler_setpoint[i] if i < len(cls.spooler_setpoint) else "",
                        cls.spooler_rpm[i] if i < len(cls.spooler_rpm) else "",
                        cls.spooler_duty_cycle[i] if i < len(cls.spooler_duty_cycle) else ""])

        print(f"CSV file {filename} generated.")

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
