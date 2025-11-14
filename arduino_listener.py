"""Интеграция с Arduino UNO и камерой для системы EcoBin.

Скрипт ожидает сигнал с цифрового пина (по умолчанию D5),
делает снимок с веб-камеры, пытается определить тип отхода,
а затем запрашивает QR-код на сервере. Используется простая
эвристика для распознавания материала; в боевом решении её
нужно заменить обученной моделью.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Optional

import cv2
import numpy as np
import requests
import serial

API_URL = os.environ.get("ECOBIN_API_URL", "http://localhost:5000/api/reward")
API_TOKEN = os.environ.get("ECOBIN_API_TOKEN", "changeme")
SERIAL_PORT = os.environ.get("ECOBIN_SERIAL_PORT", "COM3" if os.name == "nt" else "/dev/ttyACM0")
SERIAL_BAUDRATE = int(os.environ.get("ECOBIN_BAUDRATE", "9600"))
SENSOR_TRIGGER_VALUE = os.environ.get("ECOBIN_TRIGGER", "1")


def capture_frame() -> Optional[np.ndarray]:
    camera_index = int(os.environ.get("ECOBIN_CAMERA", "0"))
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print("[EcoBin] Не удалось открыть камеру", file=sys.stderr)
        return None

    success, frame = cap.read()
    cap.release()
    if not success:
        print("[EcoBin] Не удалось сделать снимок", file=sys.stderr)
        return None
    return frame


def classify_material(frame: np.ndarray) -> str:
    """Простейшая эвристика: ищем много синего цвета — считаем, что это бутылка."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask_blue = cv2.inRange(hsv, (90, 50, 50), (130, 255, 255))
    blue_ratio = mask_blue.sum() / 255 / mask_blue.size

    if blue_ratio > 0.1:
        return "bottle"

    # Бумага обычно ярче: смотрим на среднюю яркость.
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean_intensity = float(np.mean(gray))
    if mean_intensity > 150:
        return "paper"

    # Если уверенности нет — запрашиваем у оператора.
    response = input("[EcoBin] Введите тип отхода (bottle/paper): ").strip().lower()
    return "bottle" if response not in {"paper", "p"} else "paper"


def request_reward(material: str) -> Optional[dict]:
    try:
        response = requests.post(
            API_URL,
            json={"material": material},
            headers={"X-API-KEY": API_TOKEN},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[EcoBin] Ошибка запроса API: {exc}", file=sys.stderr)
        return None
    return response.json()


def wait_for_trigger() -> serial.Serial:
    ser = serial.Serial(SERIAL_PORT, SERIAL_BAUDRATE, timeout=1)
    print(f"[EcoBin] Ожидаем сигнал на {SERIAL_PORT} со скоростью {SERIAL_BAUDRATE} бод.")
    return ser


def main() -> None:
    try:
        ser = wait_for_trigger()
    except serial.SerialException as exc:
        print(f"[EcoBin] Не удалось открыть последовательный порт: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        while True:
            try:
                line = ser.readline().decode(errors="ignore").strip()
            except serial.SerialException as exc:
                print(f"[EcoBin] Ошибка чтения из порта: {exc}", file=sys.stderr)
                time.sleep(1)
                continue

            if not line:
                continue

            if line != SENSOR_TRIGGER_VALUE:
                continue

            print("[EcoBin] Обнаружено событие от датчика. Запускаем камеру...")
            frame = capture_frame()
            if frame is None:
                continue

            material = classify_material(frame)
            print(f"[EcoBin] Определён материал: {material}")

            reward = request_reward(material)
            if reward:
                print(
                    "[EcoBin] QR-код готов:"
                    f" материал={reward['material']} баллы={reward['points']}"
                    f" ссылка={reward['qr_url']}"
                )
            time.sleep(0.5)
    finally:
        ser.close()
        print("[EcoBin] Завершение работы.")


if __name__ == "__main__":
    main()
