"""Real encoder checks using synthetic color frames, never private media."""
import base64
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from module.resource_analysis.evidence import VIDEO_FRAME_FILTER, _image_data


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
class VideoFrameResolutionTests(unittest.TestCase):
    def test_model_frames_preserve_more_detail_without_enlarging_small_sources(self):
        for source, expected in (((1920, 1080), (1024, 576)), ((320, 180), (320, 180)),
                                 ((1080, 1920), (576, 1024))):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / 'frame.png'
                subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=s={source[0]}x{source[1]}', '-frames:v', '1', '-vf', VIDEO_FRAME_FILTER,
                    '-threads', '1', str(target)], check=True, capture_output=True, timeout=10)
                encoded = _image_data(target).split(',', 1)[1]
                with Image.open(io.BytesIO(base64.b64decode(encoded))) as frame:
                    self.assertEqual(expected, frame.size)


if __name__ == '__main__':
    unittest.main()
