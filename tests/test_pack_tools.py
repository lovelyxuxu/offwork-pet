import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from PIL import Image


class CreatorToolTests(unittest.TestCase):
    def test_gif_to_package_action_export_and_real_player_preview(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / 'character.gif'
            first = Image.new('RGBA', (32, 32), '#edb564')
            second = Image.new('RGBA', (32, 32), '#b6d8ce')
            first.save(image, save_all=True, append_images=[second], duration=[80, 120], loop=0)
            source, archive, preview = folder / 'source', folder / 'test.petpack', folder / 'preview.png'
            def run(*args):
                return subprocess.run([sys.executable, str(root / 'tools/character_pack.py'), *map(str, args)],
                    cwd=root, env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONUTF8='1'),
                    capture_output=True, text=True, encoding='utf-8', timeout=20,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            commands = [
                ('from-image', image, '--id', 'artist.original', '--name', 'Original', '--author', 'Artist', '--output', source),
                ('add-action', source, image, '--id', 'dance', '--label', 'Dance', '--remote'),
                ('build', source, '--output', archive), ('check', archive),
                ('preview', archive, '--snapshot', preview)]
            for command in commands:
                result = run(*command)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            behavior = json.loads((source / 'behavior.json').read_text(encoding='utf-8'))
            self.assertEqual([frame['durationMs'] for frame in behavior['animations']['rest']['frames']], [80, 120])
            self.assertEqual(behavior['interactions'][0]['id'], 'dance')
            self.assertTrue(archive.is_file())
            with Image.open(preview) as result:
                self.assertGreater(result.getchannel('A').getbbox()[2], 0)
