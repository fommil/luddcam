# we support SPI epaper devices in this file. But since the protocol
# doesn't have a vendor/product identifier we have to assume the exact
# model, documented below. This may require user settings one day to
# support multiple devices.

import numpy as np
import pygame
import os
import queue
import threading
import time

from PIL import Image, ImageOps

import mocks

# this is the api, implemented by actual implementations below
class Dummy:
    # returns a tuple of width / height of the device, or None
    def size(self):
        return None

    # this is called with the latest surface, it doesn't imply
    # that the surface has changed.
    def sync(self, surface):
        pass

    # the app is hinting that the screen could benefit from a
    # full refresh because of major changes.
    def hint(self):
        pass

    # the app is going to sleep, ignore updates until wake
    def sleep(self):
        pass

    def wake(self):
        pass

class Waveshare:
    def __init__(self, epd):
        self.epd = epd
        self.hinted = False
        self.wake()

    def size(self):
        return (self.epd.width, self.epd.height)

    def sync(self, surface):
        assert surface.get_size() == self.size()

        # throttles to 1 second differences. If this is set too short
        # we can end up creating a backlog for ourselves.
        if time.monotonic() < self.last + 1.0 / mocks.warp:
            return

        # this buf is a live view, lazily copy it if we make an update
        buf = pygame.surfarray.array3d(surface)
        def getbuf():
            self.buf = buf.copy()
            image = Image.fromarray(self.buf.transpose(1, 0, 2), mode="RGB")
            # rendering does this automatically but it's good to be explicit
            # about how we quantise to bitmap. Note that the 4GRAY mode is
            # extremely slow so not viable.
            image = image.convert("L").convert('1', dither=Image.FLOYDSTEINBERG)
            # and then invert for the epaper display
            image = ImageOps.invert(image)
            return self.epd.getbuffer(image)

        if self.hinted or self.buf is None:
            self.hinted = False
            self.epd.display_Base(getbuf())
        elif not np.array_equal(self.buf, buf):
            self.epd.display_Partial(getbuf())

    def hint(self):
        self.hinted = True

    def sleep(self):
        self.last = float('inf')
        self.epd.Clear()
        self.epd.sleep()

    def wake(self):
        self.epd.init()
        self.last = -1
        self.buf = None

def init():
    if mocks.test_mode:
        return Waveshare(AsyncEpd(mocks.EPD()))
    if not os.path.exists('/dev/spidev0.0'):
        return Dummy()
    try:
        from waveshare_epd import epd4in26
        return Waveshare(AsyncEpd(epd4in26.EPD()))
    except Exception as e:
        return Dummy()

# the epd impl blocks, this runs everything in a background thread.
class AsyncEpd:
    def __init__(self, epd):
        self.queue = queue.Queue()
        self.epd = epd
        self.width = self.epd.width
        self.height = self.epd.height
        thread = threading.Thread(target=self.run, daemon=True, name=f"e-Paper")
        thread.start()

    # this one doesn't block, so we expose it directly
    def getbuffer(self, img):
        return self.epd.getbuffer(img)

    def run(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            # print(f"e-Paper running {item[0]}")
            match item[0]:
                case "init":
                    self.epd.init()
                case "display":
                    self.epd.display(item[1])
                case "display_Base":
                    self.epd.display_Base(item[1])
                case "display_Partial":
                    self.epd.display_Partial(item[1])
                case "Clear":
                    self.epd.Clear()
                case "sleep":
                    self.epd.sleep()

    def init(self):
        self.queue.put(("init",))

    def display(self, buf):
        self.queue.put(("display", buf))

    def display_Base(self, buf):
        self.queue.put(("display_Base", buf))

    def display_Partial(self, buf):
        self.queue.put(("display_Partial", buf))

    def Clear(self):
        self.queue.put(("Clear",))

    def sleep(self):
        self.queue.put(("sleep",))
