"""Local plaintext chat log, independent of the server's configured retention."""
import time

from .core import data_dir

MAX_BYTES = 2 * 1024 * 1024


def history_path():
    return data_dir() / 'chat-history.log'


def append(direction, rally_name, text):
    line = f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {direction} → {rally_name}：{text}\n'
    try:
        path = history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
            path.write_text(''.join(lines[len(lines) // 2:]), encoding='utf-8')
        with path.open('a', encoding='utf-8') as handle:
            handle.write(line)
    except OSError:
        pass
