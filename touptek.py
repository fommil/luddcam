# touptek implementation of the Camera duck api

import ctypes
import math
import numpy as np
import time

# no debian package for libtoupcam, self-bundled:
# https://github.com/indilib/indi-3rdparty/issues/1208
#
# thankfully touptek provide a python wrapper! to simplify distribution we might
# want to copy/paste the bits we actually use.
#
# To install locally, move the udev rule into place
# and put the .so in /usr/local/lib
from libtoupcam.python import toupcam

class Toupcam:
    def __init__(self):
        pass

    def cameras(self):
        cameras = []
        for dev in toupcam.Toupcam.EnumV2():
            cameras.append(Camera(dev))
        return cameras

# the touptek api is super weird. When we start a session we have to provide
# a function to receive events. After various commands we have to wait for
# specific events to arrive, before we can proceed. We use this to simulate
# asi2 style polling.
def callback(ev, ctx):
    # print(f"got a callback about {ev}")
    if ev == toupcam.TOUPCAM_EVENT_IMAGE:
        ctx.image_event()

def gain_to_cb(gain):
    assert gain > 0
    return int(round(200.0 * math.log10(gain / 100.0)))

def cb_to_gain(cb):
    return int(round(100.0 * (10.0 ** (cb / 200.0))))

# this should look like an asi2.Camera duck
class Camera:
    def __init__(self, dev):
        self.name = dev.displayname
        model = dev.model
        self.pixelsize = model.xpixsz
        self.is_cooled = model.flag & toupcam.TOUPCAM_FLAG_TEC

        self.guide = bool(model.flag & toupcam.TOUPCAM_FLAG_ST4)

        self.d = dev

        try:
            toupcam.Toupcam.Close(dev.id)
        except Exception:
            pass

        self.c = toupcam.Toupcam.Open(dev.id)
        self.bitdepth = self.c.MaxBitDepth()

        # in terms of flipping it is best to use the SDK defaults
        # as sometimes it is to correct effectively a hardware mistake
        # self.c.put_HFlip(False)
        # self.c.put_VFlip(False)
        self.c.put_AutoExpoEnable(False)
        self.c.put_Option(toupcam.TOUPCAM_OPTION_RAW, 1)
        self.c.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
        self.c.put_Option(toupcam.TOUPCAM_OPTION_TRIGGER, 1)
        try:
            self.c.put_Option(toupcam.TOUPCAM_OPTION_BINNING, 1)
        except Exception:
            pass
        try:
            self.c.put_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL_AUTOADJUST, 0)
        except Exception:
            pass

        self.c.put_Roi(0, 0, 0, 0)

        self.has_gain = True
        self.gain = gain_to_cb(self.c.get_ExpoAGain())
        self.gain_min, self.gain_max, self.gain_default = (
            gain_to_cb(g) for g in self.c.get_ExpoAGainRange()
        )
        # TODO table of unity gains
        self.gain_unity = None

        # there isn't any way to calculate a sensible blacklevel for a given gain
        # so we just leave it at whatever default it had, exposing the value.
        if model.flag & toupcam.TOUPCAM_FLAG_BLACKLEVEL:
            self.offset = self.c.get_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL)
        else:
            self.offset = None
        # print(f"blacklevel is {self.offset}")

        exposure_min, exposure_max, exposure_default = self.c.get_ExpTimeRange()
        self.exposure_min = exposure_min / 1_000_000
        self.exposure_max = exposure_max / 1_000_000

        if model.flag & toupcam.TOUPCAM_FLAG_MONO:
            self.bayer = None
        else:
            raw, depths = self.c.get_RawFormat()
            self.bayer = raw.to_bytes(4, "little").decode("ascii")
            # print(f"bayer = {self.bayer}")

        w, h = self.c.get_Size()
        # print(f"produces {w}x{h}")
        self.bufsize = toupcam.TDIBWIDTHBYTES(w * 16) * h
        # print(f"buffer = {self.bufsize}")

        self.c.StartPullModeWithCallback(callback, self)
        self.img = None

    def image_event(self):
        try:
            # print("image event, trying to pull the image")
            buf = bytes(self.bufsize)
            self.c.PullImageV4(buf, 0, 0, 0, None)
            # print("got an image")
            w, h = self.c.get_Size()
            self.img = np.frombuffer(buf, dtype=np.uint16).reshape(h, w)

        except Exception as ex:
            print(f"touptek pullimage failed due to {ex}")
            self.img = None

    def capture_start(self, exposure):
        # print(f"touptek capture_start for exposure={exposure}")
        self.img = False
        v = int(exposure * 1_000_000) # us
        self.c.put_ExpoTime(v)
        self.c.Trigger(1)

    def capture_wait(self):
        v = self.img
        if v is None or v is False:
            return v
        return True

    def capture_finish(self):
        img = self.img
        self.img = None
        return img

    def capture_stop(self):
        self.c.Trigger(0)
        self.img = None

    def set_gain(self, cb):
        gain = cb_to_gain(cb)
        self.c.put_ExpoAGain(gain)
        self.gain = gain_to_cb(self.c.get_ExpoAGain())

    def get_temp(self):
        if self.d.model.flag & toupcam.TOUPCAM_FLAG_GETTEMPERATURE:
            return self.c.get_Temperature()

    def set_cooling(self, target):
        self.c.set_Option(toupcam.TOUPCAM_OPTION_TECTARGET, target)

# minimal test, check we can make an exposure on a single camera
if __name__ == '__main__':
    api = Toupcam()
    cameras = api.cameras()
    assert len(cameras) > 0
    camera = cameras[0]

    #camera.set_cooling(0)
    # print(f"gain range = ({camera.gain_min}, {camera.gain_default}, {camera.gain_max}), unity = {camera.gain_unity}")
    camera.set_gain(camera.gain_unity or 10)

    # time.sleep(10)
    print(f"seen {camera.name} ({camera.pixelsize:.2f} µm) (exps = {camera.exposure_min}...{camera.exposure_max}) [guide={camera.guide}]")
    print(f"temp = {camera.get_temp()}, gain = {camera.gain}, gain_bounds = {camera.gain_min},{camera.gain_max}")

    camera.capture_start(1)
    time.sleep(1)
    while True:
        status = camera.capture_wait()
        print(f"status = {status}")
        if status == False:
            #print("trying to stop the capture")
            #camera.capture_stop()
            time.sleep(0.1)
            continue
        else:
            break

# Local Variables:
# compile-command: "LD_LIBRARY_PATH=libtoupcam/linux/x64 python3 touptek.py"
# End:
