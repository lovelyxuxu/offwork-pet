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
    y: float | None = None
    velocity_y: float = 0
    support: object = None

    def _step(self, elapsed, scale, scene):
        radius = 16*scale
        self.left, self.right = scene.bounds.left+radius, scene.bounds.right-radius
        if self.support is not None:
            matches = [s for s in scene.surfaces if s.key == self.support.key]
            current = next((s for s in matches if s.left <= self.x+s.origin_x-self.support.origin_x <= s.right), None)
            if current:
                self.x += current.origin_x-self.support.origin_x
                self.y = current.y-radius
            self.support = current
        old_x, old_y = self.x, self.y
        self.x += self.velocity*elapsed
        if self.x < self.left or self.x > self.right:
            self.x = max(self.left, min(self.right, self.x))
            self.velocity *= -.65
        if self.support and not self.support.left <= self.x <= self.support.right:
            self.support = None
        if self.support is None:
            self.y += self.velocity_y*elapsed + 550*scale*elapsed**2
            self.velocity_y += 1100*scale*elapsed
            if self.y < scene.bounds.top+radius:
                self.y = scene.bounds.top+radius
                self.velocity_y = abs(self.velocity_y)*.5
            if self.velocity_y >= 0:
                contacts = []
                for surface in scene.surfaces:
                    if old_y+radius <= surface.y+.01 and self.y+radius >= surface.y:
                        t = (surface.y-radius-old_y)/max(.0001, self.y-old_y)
                        hit_x = old_x+(self.x-old_x)*max(0, min(1, t))
                        if surface.left <= hit_x <= surface.right:
                            contacts.append(surface)
                if contacts:
                    surface = min(contacts, key=lambda s: s.y)
                    self.y = surface.y-radius
                    self.velocity_y *= -.38
                    if abs(self.velocity_y) < 55*scale:
                        self.velocity_y, self.support = 0, surface
        self.velocity *= math.exp(-elapsed*(1.1 if self.support else .08))
        if self.support and abs(self.velocity) < 3*scale:
            self.velocity = 0
        self.angle += (self.x-old_x)/radius*180/math.pi

    def advance(self, elapsed, scale=1, scene=None):
        elapsed = min(.1, max(0, elapsed))
        if scene is not None and self.y is not None:
            steps = max(1, math.ceil(elapsed*120))
            for _ in range(steps):
                self._step(elapsed/steps, scale, scene)
            return
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
        self.scene = None
        self.pet_floor = None

    def stop(self):
        self.runtime.cancel('toy')
        self.playing = False

    def hold(self):
        self.runtime.cancel('toy')
        self.dragging = True
        self.ball.velocity = 0
        self.ball.velocity_y = 0
        self.ball.support = None

    def release(self, velocity, velocity_y=0):
        self.dragging = False
        self.runtime.cancel('toy')
        self.ball.velocity = max(-600*self.scale, min(600*self.scale, velocity))
        self.ball.velocity_y = max(-1400*self.scale, min(1400*self.scale, velocity_y))
        self.ball.support = None
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
        self.ball.advance(elapsed, self.scale, self.scene)
        if not self.playing:
            return position
        finishing_touch = (self.runtime.current == self.spec['touchAction']
                           and self.runtime.context.get('source') == 'toy')
        if now >= self.deadline or self.touches >= 3 and not finishing_touch or self.runtime.state not in self.spec['states']:
            self.stop()
            return position
        current = self.runtime.current
        ours = current and self.runtime.context.get('source') == 'toy'
        if (self.ball.y is not None and self.pet_floor is not None
                and (self.ball.support is None or abs(self.ball.y+16*self.scale-self.pet_floor) > 24*self.scale)):
            if ours:
                self.runtime.cancel('toy')
            return position
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
