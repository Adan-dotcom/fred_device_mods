"""File to setup the layout of the User Interface"""
import threading
from typing import Tuple
from PyQt5.QtWidgets import (QApplication, QWidget, QGridLayout, QLabel,
                             QDoubleSpinBox, QSpinBox, QSlider, QPushButton, QMessageBox,
                             QLineEdit, QScrollArea, QVBoxLayout)
from PyQt5.QtCore import QTimer, Qt, QObject, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from database import Database
from fiber_camera import FiberCamera


class _Messenger(QObject):
    """Relays messages from any thread to the GUI thread via a Qt signal.
    Creating dialogs directly from the hardware thread crashes Qt
    (segmentation fault on the RPi)."""
    message = pyqtSignal(str, str)


class UserInterface():
    """"Graphical User Interface Class"""
    def __init__(self) -> None:
        self.app = QApplication([])
        self.window = QWidget()
        self.layout = QGridLayout()

        # Cross-thread message relay: emitting the signal is safe from any
        # thread; the connected slot runs in this (GUI) thread.
        self._messenger = _Messenger()
        self._messenger.message.connect(self._show_message_on_gui_thread)

        self.motor_plot, self.temperature_plot, self.diameter_plot \
            = self.add_plots()

        self.target_diameter, self.diameter_kp, self.diameter_ki, \
            self.diameter_kd = self.add_diameter_controls()

        self.motor_kp, self.motor_ki, self.motor_kd, \
            self.extrusion_motor_speed = self.add_motor_controls()

        self.target_temperature_label, self.target_temperature, \
            self.temperature_kp, self.temperature_ki, self.temperature_kd \
            = self.add_temperature_controls()

        self.fan_duty_cycle_label, self.fan_duty_cycle = self.add_fan_controls()

        self.vision_blur, self.vision_threshold = self.add_vision_controls()

        self.csv_filename = QLineEdit()
        self.csv_filename.setText("Enter a file name")
        self.layout.addWidget(self.csv_filename, 30, 6)

        self.spooling_control_state = False
        self.device_started = False
        self.start_motor_calibration = False

        self.fiber_camera = FiberCamera(self.target_diameter, self.vision_blur,
                                        self.vision_threshold)
        if self.fiber_camera.diameter_coefficient == -1:
            self.show_message("Camera calibration data not found",
                              "Please calibrate the camera.")
            self.fiber_camera.diameter_coefficient = 0.00782324
        self.layout.addWidget(self.fiber_camera.raw_image, 2, 8, 11, 1)
        self.layout.addWidget(self.fiber_camera.processed_image, 13, 8, 11, 1)

        self.add_buttons()

        container = QWidget()
        container.setLayout(self.layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(False)
        scroll_area.setWidget(container)

        window_layout = QVBoxLayout()
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.addWidget(scroll_area)
        self.window.setLayout(window_layout)

        self.window.setWindowTitle("MIT FrED")
        self.window.setGeometry(100, 100, 1600, 900)
        self.window.setMinimumSize(900, 600)

    def add_plots(self):
        """Add plots to the layout"""
        motor_plot = self.Plot("DC Spooling Motor", "Speed (RPM)")
        temperature_plot = self.Plot("Temperature", "Temperature (C)")
        diameter_plot = self.Plot("Diameter", "Diameter (mm)")

        self.layout.addWidget(diameter_plot, 2, 0, 8, 4)
        self.layout.addWidget(motor_plot, 11, 0, 8, 4)
        self.layout.addWidget(temperature_plot, 19, 0, 8, 4)

        return motor_plot, temperature_plot, diameter_plot

    def add_diameter_controls(self) -> Tuple[QDoubleSpinBox, QDoubleSpinBox,
                                             QDoubleSpinBox, QDoubleSpinBox]:
        """Add UI spin boxes to control the diameter PID"""
        font_style = "font-size: %ipx; font-weight: bold;"

        target_diameter_label = QLabel("Target Diameter (mm)")
        target_diameter_label.setStyleSheet(font_style % 16)
        target_diameter = QDoubleSpinBox()
        target_diameter.setMinimum(0.3)
        target_diameter.setMaximum(0.6)
        target_diameter.setValue(0.35)
        target_diameter.setSingleStep(0.01)
        target_diameter.setDecimals(2)

        diameter_kp_label = QLabel("Diameter Kp")
        diameter_kp_label.setStyleSheet(font_style % 14)
        diameter_kp = QDoubleSpinBox()
        diameter_kp.setMinimum(0.0)
        diameter_kp.setMaximum(5.0)
        diameter_kp.setValue(0.72)
        diameter_kp.setSingleStep(0.01)
        diameter_kp.setDecimals(3)

        diameter_ki_label = QLabel("Diameter Ki")
        diameter_ki_label.setStyleSheet(font_style % 14)
        diameter_ki = QDoubleSpinBox()
        diameter_ki.setMinimum(0.0)
        diameter_ki.setMaximum(5.0)
        diameter_ki.setValue(1.8)
        diameter_ki.setSingleStep(0.01)
        diameter_ki.setDecimals(3)

        diameter_kd_label = QLabel("Diameter Kd")
        diameter_kd_label.setStyleSheet(font_style % 14)
        diameter_kd = QDoubleSpinBox()
        diameter_kd.setMinimum(0.0)
        diameter_kd.setMaximum(2.0)
        diameter_kd.setValue(0.072)
        diameter_kd.setSingleStep(0.001)
        diameter_kd.setDecimals(3)

        self.layout.addWidget(target_diameter_label, 2, 6)
        self.layout.addWidget(target_diameter, 3, 6)
        self.layout.addWidget(diameter_kp_label, 4, 6)
        self.layout.addWidget(diameter_kp, 5, 6)
        self.layout.addWidget(diameter_ki_label, 6, 6)
        self.layout.addWidget(diameter_ki, 7, 6)
        self.layout.addWidget(diameter_kd_label, 8, 6)
        self.layout.addWidget(diameter_kd, 9, 6)
        return target_diameter, diameter_kp, diameter_ki, diameter_kd

    def add_motor_controls(self) -> Tuple[QDoubleSpinBox, QDoubleSpinBox,
                                          QDoubleSpinBox, QDoubleSpinBox]:
        """Add UI spin boxes to control the spooler and extruder motors"""
        font_style = "font-size: %ipx; font-weight: bold;"

        motor_kp_label = QLabel("DC Motor Kp")
        motor_kp_label.setStyleSheet(font_style % 14)
        motor_kp = QDoubleSpinBox()
        motor_kp.setMinimum(0.0)
        motor_kp.setMaximum(5.0)
        motor_kp.setValue(0.24)
        motor_kp.setSingleStep(0.01)
        motor_kp.setDecimals(3)

        motor_ki_label = QLabel("DC Motor Ki")
        motor_ki_label.setStyleSheet(font_style % 14)
        motor_ki = QDoubleSpinBox()
        motor_ki.setMinimum(0.0)
        motor_ki.setMaximum(5.0)
        motor_ki.setValue(0.533)
        motor_ki.setSingleStep(0.01)
        motor_ki.setDecimals(3)

        motor_kd_label = QLabel("DC Motor Kd")
        motor_kd_label.setStyleSheet(font_style % 14)
        motor_kd = QDoubleSpinBox()
        motor_kd.setMinimum(0.0)
        motor_kd.setMaximum(2.0)
        motor_kd.setValue(0.027)
        motor_kd.setSingleStep(0.001)
        motor_kd.setDecimals(3)

        extrusion_motor_speed_label = QLabel("Extrusion Motor Speed (RPM)")
        extrusion_motor_speed_label.setStyleSheet(font_style % 16)
        extrusion_motor_speed = QDoubleSpinBox()
        extrusion_motor_speed.setMinimum(0.0)
        extrusion_motor_speed.setMaximum(2.0)
        extrusion_motor_speed.setValue(1.2)
        extrusion_motor_speed.setSingleStep(0.1)
        extrusion_motor_speed.setDecimals(1)

        self.layout.addWidget(motor_kp_label, 10, 6)
        self.layout.addWidget(motor_kp, 11, 6)
        self.layout.addWidget(motor_ki_label, 12, 6)
        self.layout.addWidget(motor_ki, 13, 6)
        self.layout.addWidget(motor_kd_label, 14, 6)
        self.layout.addWidget(motor_kd, 15, 6)
        self.layout.addWidget(extrusion_motor_speed_label, 16, 6)
        self.layout.addWidget(extrusion_motor_speed, 17, 6)
        return motor_kp, motor_ki, motor_kd, extrusion_motor_speed

    def add_temperature_controls(self) -> Tuple[QLabel, QSlider, QDoubleSpinBox,
                                                QDoubleSpinBox, QDoubleSpinBox]:
        """Add UI controls for the temperature"""
        font_style = "font-size: %ipx; font-weight: bold;"

        target_temperature_label = QLabel("Temperature (C)")
        target_temperature_label.setStyleSheet(font_style % 16)
        target_temperature = QSlider(Qt.Horizontal)
        target_temperature.setMinimum(65)
        target_temperature.setMaximum(105)
        target_temperature.setValue(95)
        target_temperature.valueChanged.connect(self.update_temperature_slider_label)

        temperature_kp_label = QLabel("Temperature Kp")
        temperature_kp_label.setStyleSheet(font_style % 14)
        temperature_kp = QDoubleSpinBox()
        temperature_kp.setMinimum(0.0)
        temperature_kp.setMaximum(2.0)
        temperature_kp.setValue(1.4)
        temperature_kp.setSingleStep(0.1)
        temperature_kp.setDecimals(5)

        temperature_ki_label = QLabel("Temperature Ki")
        temperature_ki_label.setStyleSheet(font_style % 14)
        temperature_ki = QDoubleSpinBox()
        temperature_ki.setMinimum(0.0)
        temperature_ki.setMaximum(2.0)
        temperature_ki.setValue(0.2)
        temperature_ki.setSingleStep(0.1)
        temperature_ki.setDecimals(5)

        temperature_kd_label = QLabel("Temperature Kd")
        temperature_kd_label.setStyleSheet(font_style % 14)
        temperature_kd = QDoubleSpinBox()
        temperature_kd.setMinimum(0.0)
        temperature_kd.setMaximum(2.0)
        temperature_kd.setValue(0.8)
        temperature_kd.setSingleStep(0.1)
        temperature_kd.setDecimals(5)

        self.layout.addWidget(target_temperature_label, 18, 6)
        self.layout.addWidget(target_temperature, 19, 6)
        self.layout.addWidget(temperature_kp_label, 20, 6)
        self.layout.addWidget(temperature_kp, 21, 6)
        self.layout.addWidget(temperature_ki_label, 22, 6)
        self.layout.addWidget(temperature_ki, 23, 6)
        self.layout.addWidget(temperature_kd_label, 24, 6)
        self.layout.addWidget(temperature_kd, 25, 6)

        return target_temperature_label, target_temperature, temperature_kp, \
            temperature_ki, temperature_kd

    def add_fan_controls(self) -> Tuple[QLabel, QSlider]:
        """Add UI controls for the fan"""
        font_style = "font-size: %ipx; font-weight: bold;"
        fan_duty_cycle_label = QLabel("Fan Duty Cycle (%)")
        fan_duty_cycle_label.setStyleSheet(font_style % 14)
        fan_duty_cycle = QSlider(Qt.Horizontal)
        fan_duty_cycle.setMinimum(0)
        fan_duty_cycle.setMaximum(100)
        fan_duty_cycle.setValue(30)
        fan_duty_cycle.valueChanged.connect(self.update_fan_slider_label)

        self.layout.addWidget(fan_duty_cycle_label, 26, 6)
        self.layout.addWidget(fan_duty_cycle, 27, 6)

        return fan_duty_cycle_label, fan_duty_cycle

    def add_vision_controls(self) -> Tuple[QSpinBox, QDoubleSpinBox]:
        """Add UI controls to tune the vision algorithm in real time"""
        font_style = "font-size: %ipx; font-weight: bold;"

        blur_label = QLabel("Blur kernel (px)")
        blur_label.setStyleSheet(font_style % 14)
        blur_spinbox = QSpinBox()
        blur_spinbox.setMinimum(1)
        blur_spinbox.setMaximum(31)
        blur_spinbox.setValue(5)
        blur_spinbox.setSingleStep(2)

        threshold_label = QLabel("Detection threshold")
        threshold_label.setStyleSheet(font_style % 14)
        threshold_spinbox = QDoubleSpinBox()
        threshold_spinbox.setMinimum(0.01)
        threshold_spinbox.setMaximum(1.0)
        threshold_spinbox.setValue(0.15)
        threshold_spinbox.setSingleStep(0.01)
        threshold_spinbox.setDecimals(2)

        self.layout.addWidget(blur_label, 2, 7)
        self.layout.addWidget(blur_spinbox, 3, 7)
        self.layout.addWidget(threshold_label, 4, 7)
        self.layout.addWidget(threshold_spinbox, 5, 7)

        return blur_spinbox, threshold_spinbox

    def add_buttons(self):
        """Add buttons to the layout"""
        font_style = "background-color: green; font-size: 14px; font-weight: bold;"
        stop_style = "background-color: red; font-size: 14px; font-weight: bold;"

        spooling_control = QPushButton("Start/stop spooling close loop control")
        spooling_control.setStyleSheet(font_style)
        spooling_control.clicked.connect(self.spooling_control_toggle)

        start_device = QPushButton("Start device")
        start_device.setStyleSheet(font_style)
        start_device.clicked.connect(self.set_start_device)

        stop_device = QPushButton("Stop device")
        stop_device.setStyleSheet(stop_style)
        stop_device.clicked.connect(self.set_stop_device)

        calibrate_motor = QPushButton("Calibrate motor")
        calibrate_motor.setStyleSheet(font_style)
        calibrate_motor.clicked.connect(self.set_calibrate_motor)

        calibrate_camera = QPushButton("Calibrate camera")
        calibrate_camera.setStyleSheet(font_style)
        calibrate_camera.clicked.connect(self.set_calibrate_camera)

        download_csv = QPushButton("Download CSV File")
        download_csv.setStyleSheet(font_style)
        download_csv.clicked.connect(self.set_download_csv)

        self.layout.addWidget(spooling_control, 10, 0)
        self.layout.addWidget(start_device, 1, 0)
        self.layout.addWidget(stop_device, 1, 1)
        self.layout.addWidget(calibrate_motor, 1, 2)
        self.layout.addWidget(calibrate_camera, 1, 3)
        self.layout.addWidget(download_csv, 30, 8)

    def start_gui(self) -> None:
        """Start the GUI"""
        timer = QTimer()
        timer.timeout.connect(self.fiber_camera.camera_loop)
        timer.start(200)

        self.window.show()
        self.app.exec_()
        self.fiber_camera.release()

    def update_temperature_slider_label(self, value) -> None:
        """Update the temperature slider label"""
        self.target_temperature_label.setText(f"Temperature: {value} C")

    def update_fan_slider_label(self, value) -> None:
        """Update the fan slider label"""
        self.fan_duty_cycle_label.setText(f"Fan Duty Cycle: {value} %")

    def spooling_control_toggle(self) -> None:
        """Toggle the spooling control"""
        self.spooling_control_state = not self.spooling_control_state
        if self.spooling_control_state:
            QMessageBox.information(self.app.activeWindow(), "Spooling Control",
                                    "Spooling control started.")
        else:
            QMessageBox.information(self.app.activeWindow(), "Spooling Control",
                                    "Spooling control stopped.")

    def set_start_device(self) -> None:
        """Set start device flag"""
        self.device_started = True
        QMessageBox.information(self.app.activeWindow(), "Device Start",
                                "Device started.")

    def set_stop_device(self) -> None:
        """Stop the device"""
        self.device_started = False
        self.spooling_control_state = False
        QMessageBox.information(self.app.activeWindow(), "Device Stop",
                                "Device stopped.")

    def set_calibrate_motor(self) -> None:
        """Set calibrate motor flag"""
        QMessageBox.information(self.app.activeWindow(), "Motor Calibration",
                                "Motor is calibrating.")
        self.start_motor_calibration = True

    def set_calibrate_camera(self) -> None:
        """Run camera calibration in a background thread to avoid freezing the GUI"""
        QMessageBox.information(self.app.activeWindow(), "Camera Calibration",
                                "Camera is calibrating. Please wait...")
        threading.Thread(target=self._run_camera_calibration, daemon=True).start()

    def _run_camera_calibration(self) -> None:
        self.fiber_camera.calibrate()
        self.show_message("Calibration",
                          "Camera calibration completed. Please restart the program.")

    def set_download_csv(self) -> None:
        """Copy the streaming session log to the user-given filename"""
        destination = Database.export_csv(self.csv_filename.text())
        if destination:
            QMessageBox.information(self.app.activeWindow(), "Download CSV",
                                    f"CSV file saved to:\n{destination}")
        else:
            QMessageBox.information(self.app.activeWindow(), "Download CSV",
                                    "No data logged yet. Start the device "
                                    "first — logging begins on Start.")

    def show_message(self, title: str, message: str) -> None:
        """Show a message box. Safe to call from any thread — the dialog is
        created on the GUI thread via the messenger signal."""
        self._messenger.message.emit(title, message)

    def _show_message_on_gui_thread(self, title: str, message: str) -> None:
        QMessageBox.information(self.app.activeWindow(), title, message)

    class Plot(FigureCanvas):
        """Base class for plots"""
        # Control loops run on the hardware thread; Qt drawing must happen
        # on the GUI thread. update_plot() emits this signal (safe from any
        # thread) and the redraw runs on the GUI thread.
        data_ready = pyqtSignal(float, float, float)

        def __init__(self, title: str, y_label: str) -> None:
            self.figure = Figure()
            self.axes = self.figure.add_subplot(111)
            super(UserInterface.Plot, self).__init__(self.figure)
            self.data_ready.connect(self._update_plot_on_gui_thread)

            self.axes.set_title(title)
            self.axes.set_xlabel("Time (s)")
            self.axes.set_ylabel(y_label)

            self.progress_line, = self.axes.plot([], [], lw=2, label=title)
            self.setpoint_line, = self.axes.plot([], [], lw=2, color='r',
                                                 label=f'Target {title}')
            self.axes.legend()

            self.x_data = []
            self.y_data = []
            self.setpoint_data = []

        def update_plot(self, x: float, y: float, setpoint: float) -> None:
            """Queue a plot update. Safe to call from any thread."""
            self.data_ready.emit(x, y, setpoint)

        def _update_plot_on_gui_thread(self, x: float, y: float,
                                       setpoint: float) -> None:
            self.x_data.append(x)
            self.y_data.append(y)
            self.setpoint_data.append(setpoint)

            self.progress_line.set_label(f"{self.axes.get_title()}: {y:.1f}")
            self.axes.legend()

            self.progress_line.set_data(self.x_data, self.y_data)
            self.setpoint_line.set_data(self.x_data, self.setpoint_data)

            self.axes.relim()
            self.axes.autoscale_view()
            self.draw()
