from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import slim_project


class SlimProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root/'.gitignore').write_text('/outputs/\n/build/\n/dist/\n/work/\n')
        (self.root/'README.md').write_text('outputs/keep.png')
        for name in ('outputs', 'work', 'dist', 'build'):
            (self.root/name).mkdir()
        self.mock_root = patch.object(slim_project, 'ROOT', self.root)
        self.mock_root.start()

    def tearDown(self):
        self.mock_root.stop()
        self.temp.cleanup()

    def test_source_extra_is_rejected_without_deletion(self):
        with self.assertRaises(ValueError):
            slim_project.collect(['README.md'])
        self.assertTrue((self.root/'README.md').exists())

    def test_tracked_ignored_artifact_is_rejected(self):
        target = self.root/'outputs/tracked.png'
        target.write_bytes(b'test')
        subprocess.run(['git', 'add', '-f', 'outputs/tracked.png'], cwd=self.root, check=True)
        with self.assertRaisesRegex(ValueError, 'Git'):
            slim_project.validate_targets([target])

    def test_unignored_file_and_outside_path_are_rejected(self):
        (self.root/'.gitignore').write_text('')
        with self.assertRaises(ValueError):
            slim_project.validate_targets([self.root/'outputs/free.png'])
        with self.assertRaises(ValueError):
            slim_project.validate_targets([self.root/'../outside.png'])

    def test_default_selection_preserves_scripts_and_referenced_previews(self):
        for name in ('outputs/free.png', 'outputs/keep.png', 'work/build.py', 'outputs/tool.py'):
            (self.root/name).write_bytes(b'test')
        self.assertEqual(slim_project.collect([]), [self.root/'outputs/free.png'])
        for name in ('outputs/keep.png', 'work/build.py'):
            with self.assertRaises(ValueError):
                slim_project.collect([name])

    def test_manifest_cannot_overwrite_source_or_tracked_artifact(self):
        with self.assertRaises(ValueError):
            slim_project.validate_manifest(self.root/'README.md')
        manifest = self.root/'outputs/safe.json'
        slim_project.validate_manifest(manifest)
        manifest.write_text('{}')
        subprocess.run(['git', 'add', '-f', 'outputs/safe.json'], cwd=self.root, check=True)
        with self.assertRaises(ValueError):
            slim_project.validate_manifest(manifest)

    def test_symlink_cannot_escape_workspace(self):
        with TemporaryDirectory() as outside:
            link = self.root/'outputs/link'
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('Windows symlink permission unavailable')
            with self.assertRaises(ValueError):
                slim_project.validate_targets([link/'data.png'])
