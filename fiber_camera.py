"""Module to process video from camera to obtain the fiber diameter and display it"""
import time
import cv2
import numpy as np
from typing import Optional, Tuple
from PyQt5.QtWidgets import QLabel, QDoubleSpinBox, QSpinBox
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QWidget

from database import Database


class FiberCamera(QWidget):
    """Process video from camera to obtain the fiber diameter and display it"""

    def __init__(self, target_diameter: QDoubleSpinBox, blur_spinbox: QSpinBox,
                 threshold_spinbox: QDoubleSpinBox) -> None:
        super().__init__()
        self.raw_image = QLabel()
        self.processed_image = QLabel()
        self.target_diameter = target_diameter
        self.blur_spinbox = blur_spinbox
        self.threshold_spinbox = threshold_spinbox
        self.capture = cv2.VideoCapture(0)
        self.diameter_coefficient = Database.get_calibration_data("diameter_coefficient")
        self.previous_time = 0.0

    def camera_loop(self) -> None:
        """Loop to capture and process frames from the camera"""
        if not self.capture.isOpened():
            return
        try:
            success, frame = self.capture.read()
            if not success:
                return

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, _, _ = frame.shape
            frame = frame[height // 4:3 * height // 4, :]

            blur_k = self.blur_spinbox.value()
            if blur_k % 2 == 0:
                blur_k += 1
            threshold = self.threshold_spinbox.value()

            blurred, left_edge, right_edge = self.column_projection(frame, blur_k, threshold)

            Database.diameter_delta_time.append(time.time() - self.previous_time)
            self.previous_time = time.time()

            if left_edge is not None and right_edge is not None and right_edge > left_edge:
                fiber_diameter = (right_edge - left_edge) * self.diameter_coefficient
                Database.diameter_readings.append(fiber_diameter)
                Database.diameter_setpoint.append(self.target_diameter.value())
                Database.vision_left_edge.append(left_edge)
                Database.vision_right_edge.append(right_edge)

            annotated = self.draw_edges(frame, left_edge, right_edge)
            image_for_gui = QImage(annotated, annotated.shape[1], annotated.shape[0],
                                   QImage.Format_RGB888)
            self.raw_image.setPixmap(QPixmap(image_for_gui))

            image_for_gui = QImage(blurred, blurred.shape[1], blurred.shape[0],
                                   QImage.Format_Grayscale8)
            self.processed_image.setPixmap(QPixmap(image_for_gui))
        except Exception as e:
            print(f"Camera error: {e}")

    def column_projection(self, frame: np.ndarray, blur_kernel: int,
                          threshold: float) -> Tuple[np.ndarray, Optional[int], Optional[int]]:
        """Average pixel value per column to locate fiber edges.

        The fiber appears as a bright vertical stripe — columns inside the fiber
        have a much higher mean intensity than background columns.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (blur_kernel, blur_kernel), 0)

        projection = blurred.mean(axis=0) / 255.0
        above = projection > threshold

        if not np.any(above):
            return blurred, None, None

        left_edge = int(np.argmax(above))
        right_edge = int(len(above) - 1 - np.argmax(above[::-1]))
        return blurred, left_edge, right_edge

    def draw_edges(self, frame: np.ndarray, left_edge: Optional[int],
                   right_edge: Optional[int]) -> np.ndarray:
        """Draw vertical lines at the detected fiber edges."""
        annotated = frame.copy()
        if left_edge is not None:
            cv2.line(annotated, (left_edge, 0), (left_edge, frame.shape[0]), (255, 0, 0), 2)
        if right_edge is not None:
            cv2.line(annotated, (right_edge, 0), (right_edge, frame.shape[0]), (0, 255, 0), 2)
        return annotated

    def calibrate(self) -> None:
        """Calibrate the camera using 20 samples of a 0.45 mm reference object."""
        num_samples = 20
        accumulated_width = 0
        valid_samples = 0

        blur_k = self.blur_spinbox.value()
        if blur_k % 2 == 0:
            blur_k += 1
        threshold = self.threshold_spinbox.value()

        for _ in range(num_samples):
            success, frame = self.capture.read()
            if not success:
                continue
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            _, left_edge, right_edge = self.column_projection(frame, blur_k, threshold)
            if left_edge is not None and right_edge is not None and right_edge > left_edge:
                accumulated_width += right_edge - left_edge
                valid_samples += 1

        if valid_samples == 0 or accumulated_width == 0:
            print("Camera calibration failed: no fiber detected.")
            return

        average_width = accumulated_width / valid_samples
        print(f"Average width: {average_width:.1f} px")
        self.diameter_coefficient = 0.45 / average_width
        print(f"Diameter coefficient: {self.diameter_coefficient:.6f} mm/px")
        Database.update_calibration_data("diameter_coefficient", str(self.diameter_coefficient))

    def release(self) -> None:
        """Release the camera resource."""
        self.capture.release()
