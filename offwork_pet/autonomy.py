"""Autonomous activity scheduling and bounded motion, driven by package declarations."""
import random
from time import monotonic
from .exploration import Explorer

class AutonomyController:
    def __init__(self, runtime, clock=monotonic, rng=None):
        self.runtime, self.clock = runtime, clock
        self.rng = rng or random.Random()
        self.spec = runtime.data.get('autonomy', {})
        self.next_due = None
        self.last_action = None
        self.active = False
        self.pending_action = None
        self.support = None
        self.home = None
        self.travel = 0
        self.last_tick = clock()
        self.settings = None
        self.was_blocked = False
        self.explorer = Explorer(runtime, clock, self.rng)

    def delay(self, config):
        self.next_due = self.clock() + self.rng.uniform(config.activity_min_seconds, config.activity_max_seconds)

    def interrupt(self, config, detach=False):
        self.explorer.interrupt(config)
        playing = self.runtime.current and self.runtime.context.get('source') == 'autonomous'
        if playing:
            self.runtime.cooldowns.pop(self.runtime.current, None)
        if (playing or any(request.context.get('source') == 'autonomous' for request in self.runtime.queue)
                or any(context.get('source') == 'autonomous' for _, context in self.runtime.effects)):
            self.runtime.cancel('autonomous')
        self.active = False
        self.pending_action = None
        self.travel = 0
        if detach:
            self.support = self.home = None
        self.delay(config)

    def _surface(self, scene, position, foot, size, scale):
        x, y = position[0]+foot[0], position[1]+foot[1]
        margin = min(size[0]/2, 110*scale)
        matching = [surface for surface in scene.surfaces
                    if surface.left+margin <= x+(surface.origin_x-self.support.origin_x
                        if self.support and surface.key == self.support.key else 0) <= surface.right-margin
                    and surface.y >= scene.bounds.top+foot[1]]
        if self.support:
            same = [surface for surface in matching if surface.key == self.support.key]
            if same:
                return min(same, key=lambda surface: abs(surface.y-y))
        below = [surface for surface in matching if surface.y >= y-24*scale]
        return min(below, key=lambda surface: abs(surface.y-y)) if below else None

    def update(self, config, scene, position, foot, size, scale, blocked=False):
        """Return a logical window position, or None. Timers never catch up missed work."""
        now = self.clock()
        elapsed = min(.1, max(0, now-self.last_tick))
        self.last_tick = now
        settings = (config.autonomous_enabled, config.activity_min_seconds,
                    config.activity_max_seconds, config.roaming_mode)
        if settings != self.settings:
            self.settings = settings
            self.interrupt(config, detach=True)
        allowed = (config.autonomous_enabled and config.interactive and self.spec
                   and not self.runtime.stopped and not self.runtime.suspended)
        blocked = blocked or scene.fullscreen or not allowed
        if blocked:
            if not self.was_blocked or self.active:
                self.interrupt(config)
            self.was_blocked = True
            return None
        if self.was_blocked:
            self.delay(config)
        self.was_blocked = False
        if self.home is None:
            self.home = position
        destination = self.explorer.update(config, scene, position, foot, size, scale, self.support)
        if destination is not None:
            self.support = self.explorer.support
            self.delay(config)
            return destination
        current = self.runtime.current
        autonomous = current and self.runtime.context.get('source') == 'autonomous'
        if self.active and not autonomous:
            self.active = False
            self.delay(config)
        # Manual/remote/package actions always take precedence, even if not interruptible.
        if current and not autonomous or self.runtime.queue:
            self.delay(config)
            return None
        x, y = position
        moved = False
        if self.support and config.roaming_mode == 'free':
            support = self._surface(scene, position, foot, size, scale)
            if support:
                # Follow the window's own movement; visibility segments may change independently.
                if support.key == self.support.key:
                    x += support.origin_x-self.support.origin_x
                x = max(scene.bounds.left, min(scene.bounds.right-size[0], x))
                x = max(support.left+min(size[0]/2, 110*scale)-foot[0],
                        min(support.right-min(size[0]/2, 110*scale)-foot[0], x))
                target_y = support.y-foot[1]
                y += max(-400*scale*elapsed, min(400*scale*elapsed, target_y-y))
                moved = (x, y) != position
                self.support = support
            else:
                self.support = None
        if autonomous:
            motion = self.runtime.action_spec().get('motion')
            if motion and config.roaming_mode != 'off':
                action_elapsed = now-self.runtime.started
                if motion.get('startMs', 0)/1000 <= action_elapsed < motion['endMs']/1000:
                    if config.roaming_mode == 'free' and not self.support:
                        self.support = self._surface(scene, position, foot, size, scale)
                    if config.roaming_mode == 'free' and not self.support:
                        self.interrupt(config)
                        return (x, y) if moved else None
                    if self.support and abs(y+foot[1]-self.support.y) > 2*scale:
                        # Land first; do not play a walking cycle while floating down.
                        self.runtime.cancel('autonomous')
                        self.runtime.cooldowns.pop(current, None)
                        self.pending_action = current
                        self.active = False
                        self.next_due = now
                    else:
                        step = motion['direction']*min(motion['speed']*scale*elapsed, max(0, 180*scale-self.travel))
                        left, right = scene.bounds.left, scene.bounds.right-size[0]
                        if config.roaming_mode == 'stay':
                            radius = 120*scale
                            left, right = max(left, self.home[0]-radius), min(right, self.home[0]+radius)
                        elif self.support:
                            margin = min(size[0]/2, 110*scale)
                            left = max(left, self.support.left+margin-foot[0])
                            right = min(right, self.support.right-margin-foot[0])
                        destination = max(left, min(right, x+step))
                        if right < left or abs(destination-x) < .01 or self.travel >= 180*scale:
                            self.interrupt(config)
                        else:
                            self.travel += abs(destination-x)
                            x, moved = destination, True
            return (x, y) if moved else None
        if moved:
            return x, y
        if now < (self.next_due if self.next_due is not None else now):
            return None
        if self.runtime.state not in self.spec['states']:
            self.delay(config)
            return None
        choices = []
        for entry in self.spec['activities']:
            action = self.runtime.data['actions'][entry['action']]
            if action.get('motion') and config.roaming_mode == 'off':
                continue
            if now < self.runtime.cooldowns.get(entry['action'], 0) or not self.runtime.condition(action.get('when', {})):
                continue
            choices.append(entry)
        if not choices:
            self.delay(config)
            return None
        preferences = getattr(self.runtime, 'preferences', {})
        weights = [entry.get('weight', 1)*(.2 if entry['action'] == self.last_action else 1)
                   * (1+min(1, preferences.get(entry['action'], 0)/20)) for entry in choices]
        selected = (self.pending_action if any(entry['action'] == self.pending_action for entry in choices)
                    else self.rng.choices(choices, weights=weights)[0]['action'])
        self.pending_action = None
        result = self.runtime.request(selected, {'source': 'autonomous'})
        if result == 'started':
            self.last_action, self.active, self.travel = selected, True, 0
        else:
            self.delay(config)
        return None
