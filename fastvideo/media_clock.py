"""One precise monotonic clock for input and common audio/video deadlines.

Python 3.12 on Windows uses GetTickCount64 for time.monotonic (15.625 ms).
QueryPerformanceCounter avoids quantizing the 20 ms cadence of 50p input.
Keep this local to media scheduling; third-party transport clocks are unchanged.
"""
import time


def media_time():
    return time.perf_counter()
