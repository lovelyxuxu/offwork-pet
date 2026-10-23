"""Play the package format's WAV effects without bundling a video/media framework."""
import sys


class CharacterSound:
    def play(self, path):
        if sys.platform == 'win32':
            import winsound
            try:
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                return True
            except (RuntimeError, OSError):
                return False
        return False

    def stop(self):
        if sys.platform == 'win32':
            import winsound
            try:
                winsound.PlaySound(None, 0)
            except (RuntimeError, OSError):
                pass
