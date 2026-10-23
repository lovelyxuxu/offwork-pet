"""Small local memories, scoped by package ID; never transmitted to the buddy."""
import json
from pathlib import Path
from time import monotonic


class CompanionMemory:
    def __init__(self, path=None, clock=monotonic):
        self.path, self.clock = Path(path) if path else None, clock
        self.data, self.last = {}, {}
        if self.path and self.path.exists():
            try:
                if self.path.stat().st_size > 1024*1024:
                    return
                source = json.loads(self.path.read_text(encoding='utf-8'))
                for key, item in list(source.items())[:256]:
                    if not isinstance(item, dict):
                        continue
                    self.data[key] = {'nickname': str(item.get('nickname', ''))[:24],
                        'interactions': max(0, min(1000000, int(item.get('interactions', 0)))),
                        'favorites': {name: max(0, min(100, int(count))) for name, count in
                            list(item.get('favorites', {}).items())[:128]}}
            except (OSError, ValueError, TypeError, AttributeError):
                self.data = {}

    def entry(self, key):
        if key not in self.data and len(self.data) >= 256:
            self.data.pop(next(iter(self.data)))
        return self.data.setdefault(key, {'nickname': '', 'interactions': 0, 'favorites': {}})

    def record(self, key, action):
        if self.clock()-self.last.get(key, -float('inf')) < 10:
            return
        self.last[key] = self.clock()
        item = self.entry(key)
        item['interactions'] = min(1000000, item['interactions']+1)
        favorites = item['favorites']
        if action not in favorites and len(favorites) >= 128:
            favorites.pop(next(iter(favorites)))
        favorites[action] = min(100, favorites.get(action, 0)+1)

    def describe(self, key):
        count = self.entry(key)['interactions']
        return '刚刚认识你' if count < 5 else '渐渐熟悉你的陪伴' if count < 30 else '已经很熟悉你的习惯'

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.path)
