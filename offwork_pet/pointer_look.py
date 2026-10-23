"""Bounded idle pointer-facing controller; poses and angles belong to the pack."""
import math
from time import monotonic


def angular_distance(a, b):
    return abs((a-b+180) % 360-180)


class PointerLook:
    def __init__(self, specification=None, clock=monotonic):
        self.spec = specification
        self.clock = clock
        self.poses = sorted(specification['directions'], key=lambda item: item['angle']) if specification else []
        self.index = None
        self.vector = self.last_point = None
        self.last_motion = float('-inf')
        self.last_tick = self.since = clock()
        self.blocked_until = 0

    @property
    def animation(self):
        return self.poses[self.index]['animation'] if self.index is not None else None

    def reset(self, delay_ms=0):
        self.index = self.vector = None
        self.blocked_until = self.clock() + delay_ms/1000

    def pause(self):
        self.reset(self.spec.get('resumeDelayMs', 350) if self.spec else 350)

    def update(self, point, enabled=True):
        if not self.spec:
            return
        now = self.clock()
        elapsed = min(.25, max(0, now-self.last_tick))
        self.last_tick = now
        if self.last_point is None or math.dist(point, self.last_point) >= 1:
            self.last_motion, self.last_point = now, point
        if not enabled:
            self.pause()
            return
        if now < self.blocked_until:
            return
        dx, dy = point[0]-self.spec['origin'][0], point[1]-self.spec['origin'][1]
        distance = math.hypot(dx, dy)
        if (distance < self.spec.get('deadZone', 20) or distance > self.spec.get('maxDistance', 1600)
                or now-self.last_motion >= self.spec.get('idleTimeoutMs', 3000)/1000):
            self.reset()
            return
        if self.vector is None:
            self.vector = (dx, dy)
        else:
            factor = 1-math.exp(-elapsed/(self.spec.get('smoothingMs', 120)/1000))
            self.vector = tuple(old+(target-old)*factor for old, target in zip(self.vector, (dx, dy)))
        angle = math.degrees(math.atan2(self.vector[0], -self.vector[1])) % 360
        target = min(range(len(self.poses)), key=lambda i: angular_distance(angle, self.poses[i]['angle']))
        if self.index is not None:
            current_distance = angular_distance(angle, self.poses[self.index]['angle'])
            if current_distance <= angular_distance(angle, self.poses[target]['angle']) + self.spec.get('hysteresisDegrees', 5):
                return
            if now-self.since < self.spec.get('switchIntervalMs', 100)/1000:
                return
            # Move through authored neighboring poses instead of jumping across a turn.
            count = len(self.poses)
            clockwise = (target-self.index) % count
            counter = (self.index-target) % count
            target = (self.index + (1 if clockwise <= counter else -1)) % count
        if target != self.index:
            self.index, self.since = target, now
