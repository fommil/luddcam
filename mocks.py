# this file contains a bunch of mocks for testing, facilitating hard-coded
# scripts that use the app and then assert on screenshots of the app and the
# existence of output files.

import fitsio
import glob
import pathlib
import numpy as np
import os
import time
import threading

import luddcam_images

# this will be set to a specific test path when running tests (truthy)
test_mode = False

# how much faster time passes than reality
warp = 1.0

def output_dir():
    return f"test_data/{test_mode}/output"

class Mocks:
    def cameras(self):
        assert(test_mode)
        cameras = []

        exposures = f"test_data/{test_mode}/exposures"
        guides = f"test_data/{test_mode}/guides"

        if os.path.isdir(exposures):
            cameras.append(Camera(test_mode, exposures, False))
        if os.path.isdir(guides):
            cameras.append(Camera(f"{test_mode} (guide)", guides, True))

        return cameras

    def wheels(self):
        assert(test_mode)
        # TODO mock filter wheel if in the path
        return []

class Camera:
    # path is relative to the test_data dir to folders contain
    # files named in the form gain_exposure. If gain is None
    # then it's just exposure.
    def __init__(self, name, path, guide):
        self.name = name
        self.guide = guide
        exposures = glob.glob(f"{path}/*.fz")
        assert exposures, "no test data"

        self.is_cooled = False
        self.has_gain = False
        self.exposure_min = 0.0
        self.exposure_max = 120.0
        self.offset = None
        self.gain = None

        _, h = luddcam_images.load_fits(exposures[0])
        # print(h)
        self.bayer = luddcam_images.get_corrected_bayer(h)
        self.pixelsize = float(h["XPIXSZ"])
        # probably lost from the originals, not a standard
        self.bitdepth = h.get("BITDEPTH") or 14

        self.status = None
        self.exposure = None
        self.ready = None

        # pre-load all the data because otherwise it impacts
        # the playback warp (it can take a second to load each!)
        self.data = {}
        for e in exposures:
            s = pathlib.Path(e).name.split(".")[0]
            parts = s.split("_")
            if len(parts) == 2:
                gain, exposure = map(float, parts)
            elif len(parts) == 1:
                gain = None
                exposure = float(parts[0])
            img, _ = luddcam_images.load_fits(e)
            #print(f"registering data for {(gain, exposure)}")
            self.data[(gain, exposure)] = img

    def get_temp(self):
        pass

    def capture_start(self, exposure):
        self.status = False
        self.ready = time.monotonic() + exposure / warp
        self.exposure = float(exposure)

    def capture_wait(self):
        return self.ready < time.monotonic()

    def capture_stop(self):
        self.status = None
        self.ready = None

    def capture_finish(self):
        assert(self.capture_wait())
        self.capture_stop()
        key = (self.gain, self.exposure)
        #print(f"looking for data in {(self.gain, self.exposure)}")
        # we require all combinations to exist
        return self.data[key]

# the tests can read this and assert on the contents.
# it's actually a PIL.Image
epd_buf = None

# mock of a waveshare_epd
class EPD:
    def __init__(self):
        self.width = 800
        self.height = 600

    def getbuffer(self, img):
        return img # noop

    def init(self):
        pass

    def display(self, buf):
        global epd_buf
        epd_buf = buf

    def display_Base(self, buf):
        self.display(buf)

    def display_Partial(self, buf):
        self.display(buf)

    def Clear(self):
        self.display(None)

    def sleep(self):
        pass
