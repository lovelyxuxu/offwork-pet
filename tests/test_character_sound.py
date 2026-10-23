import sys
import unittest
from unittest.mock import patch

from offwork_pet.character_sound import CharacterSound


@unittest.skipUnless(sys.platform == 'win32', 'Windows WAV backend')
class CharacterSoundTests(unittest.TestCase):
    def test_async_wav_play_and_stop(self):
        import winsound
        sound = CharacterSound()
        with patch('winsound.PlaySound') as play:
            self.assertTrue(sound.play('character.wav'))
            play.assert_called_with('character.wav', winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            sound.stop()
            play.assert_called_with(None, 0)

    def test_unplayable_file_does_not_break_pet(self):
        with patch('winsound.PlaySound', side_effect=RuntimeError('invalid wave')):
            sound = CharacterSound()
            self.assertFalse(sound.play('bad.wav'))
            sound.stop()
