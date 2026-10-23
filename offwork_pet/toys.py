"""A bounded ball and package-directed pursuit, independent of Qt and role names."""
from dataclasses import dataclass
from time import monotonic
import math


@dataclass
class Ball:
    x: float
    left: float
    right: float
    velocity: float = 0
    angle: float = 0

    def advance(self, elapsed, scale=1):
        elapsed = min(.1, max(0, elapsed))
        before = self.x
        self.x += self.velocity * elapsed
        if self.x < self.left or self.x > self.right:
            self.x = max(self.left, min(self.right, self.x))
            self.velocity *= -.65
        self.velocity *= math.exp(-1.1 * elapsed)
        if abs(self.velocity) < 3 * scale:
            self.velocity = 0
        self.angle += (self.x-before) / (16*scale) * 180/math.pi


class BallGame:
    """One throw lasts at most 20 seconds and three touches; another throw restarts it."""
    def __init__(self, runtime, ball, limits, foot_x, scale=1, clock=monotonic):
        self.runtime, self.ball, self.clock = runtime, ball, clock
        self.spec = runtime.data['toys']['ball']
        self.limits, self.foot_x, self.scale = limits, foot_x, scale
        self.dragging = False
        self.deadline = clock()+20
        self.touches = 0
        self.next_touch = 0
        self.last_tick = clock()
        self.playing = True

    def stop(self):
        self.runtime.cancel('toy')
        self.playing = False

    def hold(self):
        self.runtime.cancel('toy')
        self.dragging = True
        self.ball.velocity = 0

    def release(self, velocity):
        self.dragging = False
        self.runtime.cancel('toy')
        self.ball.velocity = max(-600*self.scale, min(600*self.scale, velocity))
        self.deadline = self.clock()+20
        self.touches, self.next_touch = 0, self.clock()+.4
        self.last_tick = self.clock()
        self.playing = True

    def update(self, position):
        now = self.clock()
        elapsed = min(.1, max(0, now-self.last_tick))
        self.last_tick = now
        if self.runtime.stopped or self.runtime.suspended:
            self.stop()
            return position
        if self.dragging:
            return position
        self.ball.advance(elapsed, self.scale)
        if not self.playing:
            return position
        finishing_touch = (self.runtime.current == self.spec['touchAction']
                           and self.runtime.context.get('source') == 'toy')
        if now >= self.deadline or self.touches >= 3 and not finishing_touch or self.runtime.state not in self.spec['states']:
            self.stop()
            return position
        current = self.runtime.current
        ours = current and self.runtime.context.get('source') == 'toy'
        if current and not ours or self.runtime.queue:
            return position
        distance = self.ball.x-(position+self.foot_x)
        direction = 1 if distance >= 0 else -1
        reach = 60*self.scale
        if abs(distance) <= reach:
            if ours and current != self.spec['touchAction']:
                self.runtime.cancel('toy')
            if self.runtime.current is None and now >= self.next_touch:
                result = self.runtime.request(self.spec['touchAction'], {'source': 'toy'})
                if result == 'started':
                    self.ball.velocity = direction*230*self.scale
                    self.touches += 1
                    self.next_touch = now+2
            return position
        key = self.spec['rightAction' if direction > 0 else 'leftAction']
        if ours and current != key and current != self.spec['touchAction']:
            self.runtime.cancel('toy')
        if self.runtime.current is None:
            self.runtime.request(key, {'source': 'toy'})
        if self.runtime.current != key or self.runtime.context.get('source') != 'toy':
            return position
        motion = self.runtime.action_spec()['motion']
        progress = now-self.runtime.started
        if not motion.get('startMs', 0)/1000 <= progress < motion['endMs']/1000:
            return position
        step = direction*min(motion['speed']*self.scale*elapsed, max(0, abs(distance)-reach))
        destination = max(self.limits[0], min(self.limits[1], position+step))
        if abs(destination-position) < .001 and abs(step) > .001:
            self.stop()
        return destination
