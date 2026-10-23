"""A pauseable local focus timer, independent of the off-work schedule."""
import math
from time import monotonic


class FocusSession:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.deadline = None
        self.paused_seconds = None

    @property
    def active(self):
        return self.deadline is not None or self.paused_seconds is not None

    @property
    def remaining(self):
        return self.paused_seconds if self.paused_seconds is not None else max(0, math.ceil(self.deadline-self.clock())) if self.deadline is not None else 0

    def start(self, minutes):
        if type(minutes) is not int or not 1 <= minutes <= 180:
            raise ValueError('专注时长须为 1–180 分钟')
        self.deadline, self.paused_seconds = self.clock()+minutes*60, None

    def pause(self):
        if self.deadline is not None:
            self.paused_seconds, self.deadline = self.remaining, None

    def resume(self):
        if self.paused_seconds is not None:
            self.deadline, self.paused_seconds = self.clock()+self.paused_seconds, None

    def cancel(self):
        self.deadline = self.paused_seconds = None

    def tick(self):
        if self.deadline is not None and self.remaining == 0:
            self.cancel()
            return True
        return False
