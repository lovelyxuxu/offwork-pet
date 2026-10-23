"""Bounded declarative character behavior; no role names or executable package code."""
from collections import deque
from dataclasses import dataclass
import operator
import random
from time import monotonic
import uuid

from .packages import PackageError
from .pointer_look import PointerLook


@dataclass
class ActionRequest:
    id: str
    expires: float
    context: dict
    when: dict
    order: int


class BehaviorRuntime:
    def __init__(self, package, clock=monotonic, rng=None):
        self.package, self.clock = package, clock
        self.data = package.behavior
        self.revision = uuid.uuid4().hex
        self.state = self.data['initialState']
        self.state_elapsed = 0.0
        self.base_elapsed = 0.0
        self.last_tick = self.clock()
        self.inactive_elapsed = 0.0
        self.current = None
        self.started = self.clock()
        self.return_state = self.state
        self.queue = []
        self.cooldowns = {}
        self.effects = deque(maxlen=32)
        self.outcomes = deque(maxlen=64)
        self.variables = {key: item['default'] for key, item in self.data.get('variables', {}).items()}
        self.rng = rng or random.Random()
        self.suspended = False
        self.stopped = False
        self.error = ''
        self.timer_fired = {}
        self.order = 0
        self.transitions = deque(maxlen=32)
        self._recovering = False
        self._budget = 0
        self.pointer_look = PointerLook(self.data.get('pointerLook'), clock)

    def condition(self, value):
        if 'stateIn' in value and self.state not in value['stateIn']:
            return False
        if 'all' in value and not all(self.condition(v) for v in value['all']):
            return False
        if 'any' in value and not any(self.condition(v) for v in value['any']):
            return False
        if 'variable' in value:
            compare = {'eq': operator.eq, 'ne': operator.ne, 'lt': operator.lt,
                       'le': operator.le, 'gt': operator.gt, 'ge': operator.ge}[value.get('op', 'eq')]
            return compare(self.variables[value['variable']], value['value'])
        return True

    def activity(self):
        self.inactive_elapsed = 0
        for rule in self.data.get('rules', []):
            if rule['event'] == 'inactivity':
                self.timer_fired.pop(rule['id'], None)

    def action_spec(self):
        return self.data.get('actions', {}).get(self.current, {})

    def duration(self, action_id):
        action = self.data['actions'][action_id]
        animation = self.data['animations'][action['animation']]
        duration = sum(frame['durationMs'] for frame in animation['frames']) / 1000
        return action.get('maxDurationMs', duration * 1000) / 1000 if animation.get('loop') else duration

    def _start(self, request):
        self.pointer_look.pause()
        self.current = request.id
        self.context = request.context
        self.started = self.clock()
        self.return_state = self.state
        spec = self.action_spec()
        self.cooldowns[request.id] = self.started + spec.get('cooldownMs', 0) / 1000
        self.effects.append((request.id, dict(request.context)))
        self.outcomes.append(('started', request.context))

    def request(self, action_id, context=None, expires=None, when=None):
        if self.stopped or self.suspended:
            return 'paused'
        spec = self.data.get('actions', {}).get(action_id)
        if spec is None:
            return 'unsupported'
        now = self.clock()
        context, when = context or {}, when or {}
        if not self.condition(when) or not self.condition(spec.get('when', {})):
            return 'condition'
        if now < self.cooldowns.get(action_id, 0):
            return 'cooldown'
        expires = min(expires if expires is not None else float('inf'), now + spec.get('queueTtlMs', 3000) / 1000)
        if expires <= now:
            return 'expired'
        self.order += 1
        request = ActionRequest(action_id, expires, context, when, self.order)
        if self.current is None:
            self._start(request)
            return 'started'
        policy = spec.get('onBusy', 'discard')
        if policy == 'replace' and self.action_spec().get('interruptible', True) and spec.get('priority', 0) > self.action_spec().get('priority', 0):
            self.outcomes.append(('interrupted', self.context))
            self._start(request)
            return 'started'
        if policy == 'enqueue' and len(self.queue) < self.data.get('maxQueuedActions', 8) and not any(r.id == action_id for r in self.queue):
            self.queue.append(request)
            return 'queued'
        return 'busy'

    def set_state(self, identifier):
        self.pointer_look.pause()
        now = self.clock()
        if len(self.transitions) == 32 and now - self.transitions[0] < 1:
            raise PackageError('状态切换过于频繁，已停止该角色')
        self.transitions.append(now)
        if self.current:
            self.outcomes.append(('cancelled', self.context))
        self.current = None
        self.state = identifier
        self.state_elapsed = 0
        self.base_elapsed = 0
        self.timer_fired.clear()

    def invoke(self, operation, context=None, expires=None, when=None):
        self._budget += 1
        if self._budget > 32:
            raise PackageError('行为执行步数超限')
        kind = operation['type']
        if kind == 'action':
            return self.request(operation['id'], context, expires, when)
        if not self.condition(when or {}):
            return 'condition'
        if kind == 'state':
            self.set_state(operation['id'])
        elif kind == 'set':
            self.variables[operation['variable']] = operation['value']
        elif kind == 'random':
            choices = operation['choices']
            selected = self.rng.choices(choices, weights=[c.get('weight', 1) for c in choices])[0]
            return self.invoke(selected['invoke'], context, expires, when)
        return 'started'

    def entry(self, group, identifier, context=None, expires=None):
        self._budget = 0
        if self.stopped or self.suspended:
            return 'paused'
        item = next((item for item in self.data.get(group, []) if item['id'] == identifier), None)
        if item is None:
            return 'unsupported'
        return self.invoke(item['invoke'], context, expires, item.get('when', {}))

    def region(self, point):
        if point is None:
            return None
        regions = dict(self.data.get('regions', {}))
        regions.update(self.data['states'][self.state].get('regions', {}))
        regions.update(self.data['animations'][self.animation_id()].get('regions', {}))
        for key, item in sorted(regions.items(), key=lambda pair: -pair[1].get('priority', 0)):
            x, y, w, h = item['rect']
            px, py = point
            if item['shape'] == 'rect':
                hit = x <= px <= x+w and y <= py <= y+h
            else:
                hit = ((px-x-w/2)/(w/2))**2 + ((py-y-h/2)/(h/2))**2 <= 1
            if hit:
                return key
        return None

    def event(self, name, point=None, context=None):
        self._budget = 0
        if self.suspended or self.stopped:
            return 'paused'
        self.activity()
        target = self.region(point)
        rules = sorted(self.data.get('rules', []), key=lambda rule: -rule.get('rulePriority', 0))
        for rule in rules:
            if rule['event'] == name and ('region' not in rule or rule['region'] == target) and self.condition(rule.get('when', {})):
                return self.invoke(rule['then'], context, when=rule.get('when', {}))
        return 'unhandled'

    def cancel(self, source=None, reason='cancelled'):
        if source is not None and not (
                self.current and self.context.get('source') == source
                or any(request.context.get('source') == source for request in self.queue)
                or any(context.get('source') == source for _, context in self.effects)):
            return
        self.pointer_look.pause()
        self.effects = deque((item for item in self.effects if source is not None and item[1].get('source') != source), maxlen=32)
        if self.current and (source is None or self.context.get('source') == source):
            self.outcomes.append((reason, self.context))
            self.current = None
        kept = []
        for request in self.queue:
            if source is None or request.context.get('source') == source:
                self.outcomes.append((reason, request.context))
            else:
                kept.append(request)
        self.queue = kept

    def suspend(self, value):
        if value == self.suspended:
            return
        if value and not self.suspended:
            self.cancel()
            self.effects.clear()
        self.suspended = value
        self.last_tick = self.clock()

    def fail(self, error):
        self.error = str(error)
        self.cancel()
        recovery = self.data.get('recoveryState')
        if recovery is not None and not self._recovering:
            self._recovering = True
            # Recovery must not re-enter the transition limiter that caused the failure.
            self.state = recovery
            self.state_elapsed = self.base_elapsed = 0
            self.timer_fired.clear()
            self.transitions.clear()
        else:
            self.stopped = True

    def tick(self):
        now = self.clock()
        elapsed = max(0, now-self.last_tick)
        self.last_tick = now
        if self.suspended or self.stopped:
            return
        self._budget = 0
        self.inactive_elapsed += elapsed
        if self.current:
            if now-self.started >= self.duration(self.current):
                self.pointer_look.pause()
                after = self.action_spec()['after']
                self.outcomes.append(('completed', self.context))
                self.current = None
                if after['type'] == 'state':
                    self.set_state(after['id'])
                else:
                    self.state = self.return_state
                    self.base_elapsed = 0
        else:
            self.state_elapsed += elapsed
            self.base_elapsed += elapsed
        pending, self.queue = self.queue, []
        for request in sorted(pending, key=lambda r: (-self.data['actions'][r.id].get('priority', 0), r.order)):
            if request.expires <= now:
                self.outcomes.append(('expired', request.context))
            elif not self.condition(request.when) or not self.condition(self.data['actions'][request.id].get('when', {})) or now < self.cooldowns.get(request.id, 0):
                self.outcomes.append(('cancelled', request.context))
            elif self.current is None:
                self._start(request)
            else:
                self.queue.append(request)
        if self.current is None:
            for rule in sorted(self.data.get('rules', []), key=lambda rule: -rule.get('rulePriority', 0)):
                if rule['event'] not in ('timer', 'inactivity') or not self.condition(rule.get('when', {})):
                    continue
                time_value = self.state_elapsed if rule['event'] == 'timer' else self.inactive_elapsed
                previous = self.timer_fired.get(rule['id'])
                if time_value >= rule['afterMs']/1000 and (previous is None or rule.get('repeat', False) and time_value-previous >= rule['afterMs']/1000):
                    self.timer_fired[rule['id']] = time_value
                    self.invoke(rule['then'], when=rule.get('when', {}))
                    break

    def animation_id(self):
        if self.current:
            return self.action_spec()['animation']
        if not self.suspended and not self.stopped and self.pointer_look.animation:
            return self.pointer_look.animation
        return self.data['states'][self.state]['animation']

    def look_at(self, point, enabled=True):
        specification = self.data.get('pointerLook')
        allowed = (enabled and not self.current and not self.suspended and not self.stopped
                   and specification and self.state in specification['states'])
        self.pointer_look.update(point, bool(allowed))

    def frame(self):
        animation = self.data['animations'][self.animation_id()]
        frames = animation['frames']
        elapsed = ((self.clock()-self.started) if self.current else self.base_elapsed) * 1000
        if not self.current and not self.suspended and self.pointer_look.animation:
            elapsed = (self.clock()-self.pointer_look.since)*1000
        duration = sum(frame['durationMs'] for frame in frames)
        elapsed = elapsed % duration if animation.get('loop') else min(elapsed, duration-0.001)
        for frame in frames:
            if elapsed < frame['durationMs']:
                return frame
            elapsed -= frame['durationMs']
        return frames[-1]

    def close(self):
        self.cancel()
        self.effects.clear()
        self.stopped = True
        self.revision = uuid.uuid4().hex
