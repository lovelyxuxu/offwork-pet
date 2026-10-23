"""Short observe/travel/rest journeys selected from visible, reachable ledges."""
from time import monotonic
import random


class Explorer:
    def __init__(self, runtime, clock=monotonic, rng=None):
        self.runtime, self.clock = runtime, clock
        self.rng = rng or random.Random()
        self.spec = runtime.data.get('autonomy', {}).get('exploration')
        self.phase = None
        self.target = self.support = None
        self.visited = []
        self.next_due = clock()+60
        self.last_tick = clock()

    @property
    def active(self):
        return self.phase is not None

    def interrupt(self, config):
        self.runtime.cancel('exploration')
        self.phase, self.target = None, None
        self.next_due = self.clock()+max(30, self.rng.uniform(config.activity_min_seconds, config.activity_max_seconds)*3)
        self.last_tick = self.clock()

    def choose(self, scene, position, foot, size, scale):
        candidates = []
        x, y = position
        margin = min(size[0]/2, 110*scale)
        for surface in scene.surfaces:
            left = max(scene.bounds.left, surface.left+margin-foot[0])
            right = min(scene.bounds.right-size[0], surface.right-margin-foot[0])
            target_y = surface.y-foot[1]
            if right < left or target_y < scene.bounds.top or target_y-y < -self.spec.get('maxRise', 220)*scale:
                continue
            if target_y-y > self.spec.get('maxDrop', 350)*scale:
                continue
            nearest = max(left, min(right, x))
            if abs(nearest-x) > self.spec.get('maxDistance', 420)*scale:
                continue
            low, high = max(left, x-240*scale), min(right, x+240*scale)
            destination = self.rng.uniform(low, high) if low <= high else nearest
            if abs(destination-x)+abs(target_y-y) < 70*scale:
                continue
            candidates.append((surface, destination, target_y))
        if not candidates:
            return None
        fresh = [item for item in candidates if item[0].key not in self.visited[-3:]]
        return self.rng.choice(fresh or candidates)

    def _action(self, field):
        return self.runtime.request(self.spec[field], {'source': 'exploration'})

    def update(self, config, scene, position, foot, size, scale, support):
        now = self.clock()
        elapsed = min(.1, max(0, now-self.last_tick))
        self.last_tick = now
        if not self.spec or not config.exploration_enabled or config.roaming_mode != 'free':
            if self.active:
                self.interrupt(config)
            return None
        if self.runtime.current and self.runtime.context.get('source') != 'exploration' or self.runtime.queue:
            if self.active:
                self.interrupt(config)
            return None
        if not self.active:
            if now < self.next_due or self.runtime.state not in self.runtime.data['autonomy']['states']:
                return None
            choice = self.choose(scene, position, foot, size, scale)
            if choice is None:
                self.interrupt(config)
                return None
            self.target, self.target_x, self.target_y = choice
            self.support = support
            if self._action('observeAction') != 'started':
                self.interrupt(config)
                return None
            self.phase, self.deadline = 'observe', now+30
            return position
        if now >= self.deadline:
            self.interrupt(config)
            return position
        # Re-plan after geometry changes; never keep flying towards a stale window.
        if self.target not in scene.surfaces:
            self.runtime.cancel('exploration')
            below = [s for s in scene.surfaces if s.left <= position[0]+foot[0] <= s.right
                     and s.y >= position[1]+foot[1]-1 and s.y-foot[1] >= scene.bounds.top]
            if not below:
                self.support = None
                self.interrupt(config)
                return position
            self.target = min(below, key=lambda s: s.y)
            self.target_x, self.target_y = position[0], self.target.y-foot[1]
            self.phase = 'fall'
        if self.phase == 'observe':
            if self.runtime.current:
                return position
            self.start = position
            if abs(self.target_y-position[1]) > 16*scale:
                if self._action('jumpAction') != 'started':
                    self.interrupt(config)
                    return position
                self.phase, self.progress = 'jump', 0
            else:
                self.phase = 'walk'
        if self.phase == 'jump':
            duration = self.spec.get('jumpDurationMs', 900)/1000
            self.progress = min(duration, self.progress+elapsed)
            t = self.progress/duration
            arc = max(0, min(70*scale, min(self.start[1], self.target_y)-scene.bounds.top))
            destination = (self.start[0]+(self.target_x-self.start[0])*t,
                           self.start[1]+(self.target_y-self.start[1])*t-4*arc*t*(1-t))
            if t >= 1:
                self.support = self.target
                self.phase = 'land'
            return destination
        if self.phase == 'walk':
            distance = self.target_x-position[0]
            if abs(distance) <= 2*scale:
                self.runtime.cancel('exploration')
                self.support, self.phase = self.target, 'land'
                return self.target_x, self.target_y
            field = 'rightAction' if distance > 0 else 'leftAction'
            if self.runtime.current is None:
                self._action(field)
            if self.runtime.current == self.spec[field]:
                motion = self.runtime.action_spec()['motion']
                progress = now-self.runtime.started
                if motion.get('startMs', 0)/1000 <= progress < motion['endMs']/1000:
                    step = min(abs(distance), motion['speed']*scale*elapsed)*(1 if distance > 0 else -1)
                    return position[0]+step, self.target_y
            return position
        if self.phase == 'fall':
            y = min(self.target_y, position[1]+400*scale*elapsed)
            if abs(y-self.target_y) < 1:
                self.support, self.phase = self.target, 'land'
            return position[0], y
        if self.phase == 'land' and self.runtime.current is None:
            if self._action('restAction') != 'started':
                self.interrupt(config)
                return position
            self.phase = 'rest'
            self.visited = (self.visited+[self.target.key])[-8:]
        elif self.phase == 'rest' and self.runtime.current is None:
            self.interrupt(config)
        return position
