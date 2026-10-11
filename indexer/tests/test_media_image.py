import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from tg_indexer.media_image import load_image


class MediaImageTests(unittest.TestCase):
    def test_normal_image_is_rgb_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'image.png'
            Image.new('RGBA',(800,400),(255,0,0,128)).save(path)
            with load_image(path,max_side=320) as image:
                self.assertEqual(image.mode,'RGB')
                self.assertEqual(image.size,(320,160))

    @unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg unavailable')
    def test_animated_photo_is_decoded_without_modifying_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'animated.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=red:s=64x32:d=0.1','-c:v','mpeg4','-y',str(path)],check=True,capture_output=True)
            before=path.read_bytes()
            with load_image(path,max_side=64) as image:
                self.assertEqual(image.size,(64,32))
                self.assertGreater(image.getpixel((0,0))[0],200)
            self.assertEqual(path.read_bytes(),before)

    def test_missing_decoder_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'animated.mp4';path.write_bytes(b'synthetic')
            with patch('tg_indexer.media_image.shutil.which',return_value=None):
                with self.assertRaisesRegex(ValueError,'ffmpeg'):load_image(path)
