# The live / capture mode. When this mode is selected a Thread is spawned which
# continually updates (with minimum/maximum exposures) a buffer that is
# (optionally) displayed on the pygame surface by the main UI thread, and/or
# writes to disk. Threads are fine here because the C calls won't block the GIL
# and there's not much CPU work.
#
# This file contains both the exposure thread, and the code (that runs on the
# main UI thread) to produce the menu.
#
# The following icons are overlaid to give some indication of status:
#
# - media
# - mode
# - filter / gain
# - temp
# - guiding
#
# with a histogram and pixel saturation count (except for live).
#
# If there is no primary camera selected, this should show a stock image like an
# X that fills the screen. It should be blank if we are waiting on the first
# capture. If there have been 3 failures in a row, a more concerning stock image
# should be shown, e.g. a warning sign in the middle.
#
# SELECT and BACK work as normal (goes to settings menu / modal choice), but may
# be disabled or subject to a popup confirmation in some situations (see below).
#
# LEFT lets the user choose between single shot, continuous (i.e. intervals
# using the single shot settings), or intervals (defined in settings). A sets
# the value and returns to the LIVE view.
#
# A selects zoom sub-mode, which introduces a red frames indicating the region
# of interest, allowing the user to move it around on the live image before
# another A commits to the zoomed region (directional buttons continue to work
# when zoomed in). The zoom level is fixed to match the display size. Pressing A
# returns to the live view. An icon is shown to indicate that it is zoomed in.
#
# If the media is available, START takes pictures, saves to disk, then previews
# the latest image on the screen along with a histogram and some basic stats
# (e.g. count of pixels with maximum values). This happens in a loop in interval
# mode. START while a capture is in-progress should cancel a single or
# continuous shot, or pause (i.e. cancel but allowing picking up where left off)
# intervals, returning to LIVE.
#
# When viewing the last picture in single shot mode, A returns to LIVE and START
# will take another. RIGHT is reserved for future plate solving.
#
# When START has been pressed to capture, SELECT and BACK are disabled. But
# perhaps a pop-up could say "are you sure" where selection would cancel the
# capture.
#
# A design choice is that the settings are only accessible through the SELECT
# menu, we're not providing sub-menus. This keeps everything nice and simple.
#
# Discussion here about Siril header requirements:
# https://discuss.pixls.us/t/formats-and-headers-supported-required-by-siril/50531

from datetime import datetime, timezone
import threading
from enum import Enum
import os
from pathlib import Path
import time
import re

import fitsio
import numpy as np
import pygame

class Mode(Enum):
    LIVE = 0
    SINGLE = 1
    REPEAT = 2
    INTERVALS = 3

class Capture:
    def __init__(self, width, height, output_dir, camera, camera_settings, wheel, wheel_settings, mode):
        self.lock = threading.Lock()
        self.target_width = width
        self.target_height = height
        self.output_dir = output_dir
        self.camera = camera
        self.camera_settings = camera_settings
        self.wheel = wheel
        self.wheel_settings = wheel_settings
        self.mode = mode

        # we use a pygame surface so that we can draw icons and details
        self.surface = pygame.Surface((width, height))
        # TODO placeholder image until we receive data

        self.zoom = None
        self._pause = threading.Event()
        self._stop = threading.Event()

        self.thread = threading.Thread(target=self.run, daemon=True, name="Capture")

        if output_dir:
            pattern = re.compile(r'IMG_(\d{5})\.fits$')
            numbers = []
            for path in Path(self.output_dir).iterdir():
                match = pattern.match(path.name)
                if match:
                    numbers.append(int(match.group(1)))
            self.seq = max(numbers) + 1 if numbers else None

    def start(self):
        return self.thread.start()

    # Can be called by the UI thread to change the mode of operation.
    def set_mode(self, mode):
        with self.lock:
            self.mode = mode

    # Can be called by the UI thread to get the latest capture, resized to the
    # surface and metadata rendered directly onto it. This is produced once per
    # capture and re-used to avoid unnecessary allocations.
    def get_latest(self):
        with self.lock:
            return self.surface

    # Can be called by the UI thread to set the field of view when using digital
    # zoom. If the camera supports custom fovs this may be utilised, but it is
    # only necessary if the frameframe demands it. None will unset.
    def set_zoom(self, zoom):
        with self.lock:
            self.zoom = zoom

    # Can be called by the UI thread to pause the current capture. May stop the
    # exposure (e.g. close the shutter).
    def pause(self):
        self._pause.set()

    # Can be called by the UI thread to resume a paused capture.
    def resume(self):
        self._pause.clear()

    # Can be called by the UI thread to stop and exit the capture. Note that
    # this will NOT close the camera, but may stop the exposure (e.g. close the
    # shutter).
    def stop(self):
        self._stop.set()
        self.thread.join()

    def run(self):
        self.capturing = False

        exposure = self.camera_settings.exposure
        slot = None
        writer = None

        while not self._stop.is_set():
            time.sleep(0.1)
            if self._pause.is_set():
                if self.capturing:
                    self.camera.capture_stop()
                    self.capturing = False
                continue

            if not self.capturing:
                # TODO interval playback, changes exposure and wheel
                if self.wheel:
                    slot = self.wheel_settings.default
                    self.wheel.set_slot(slot)
                self.camera.capture_start(exposure)
                self.capturing = True
                continue

            # we could guard this until it's nearer the time to expect an
            # exposure to be ready, if this impacts the camera negatively.
            status = self.camera.capture_wait()
            if status == False:
                continue
            elif status == True:
                self.capturing = False
                print("capture complete")
                data = self.camera.capture_finish()

                if writer:
                    writer.stale()

                # TODO skip if LIVE
                if self.output_dir:
                    out = f"{self.output_dir}/IMG_{self.seq:05}.fits"
                    print(f"...saving to {out}")
                    self.seq += 1

                    metadata = []
                    if data.dtype == "uint16":
                        metadata.append(("BZERO", 32768))
                    elif data.dtype == "uint8":
                        metadata.append(("BZERO", 128))
                    metadata.append(("BSCALE", 1))
                    metadata.append(("PROGRAM", "luddcam"))
                    metadata.append(("DATE", datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')))
                    metadata.append(("EXPTIME", exposure))
                    if slot != None:
                        name = self.wheel_settings.filters[slot] or f"Slot {slot + 1}"
                        metadata.append(("FILTER", name))
                    metadata.append(("XPIXSZ", self.camera.pixelsize))
                    metadata.append(("YPIXSZ", self.camera.pixelsize))
                    metadata.append(("INSTRUME", self.camera.name))
                    if (temp := self.camera.get_temp()) != None:
                        metadata.append(("CCD-TEMP", temp))
                    if self.camera.is_cooled:
                        metadata.append(("SET-TEMP", self.camera_settings.cooling))
                    if self.camera.gain != None:
                        metadata.append(("GAIN", self.camera.gain))
                    if self.camera.offset != None:
                        metadata.append(("OFFSET", self.camera.gain))
                    if self.camera.bayer:
                        metadata.append(("BAYERPAT", self.camera.bayer))

                    writer = FitsWriter(data, out, self.surface, metadata)
                    writer.start()

                img_rgb = self.viewable_array(data)
                with self.lock:
                    pygame.surfarray.blit_array(self.surface, img_rgb)
            else:
                self.capturing = False
                print("capture failed")
                # TODO placeholder image to indicate something went wrong

        print(f"capture stopped for {self.camera.name}")

    def viewable_array(self, img_array):
        # in order to be able to use blit_array the dims must match the target
        # dims exactly.
        height, width = img_array.shape

        scale_w = width / self.target_width
        scale_h = height / self.target_height
        scale = min(scale_w, scale_h)

        step = max(1, int(scale))
        img_ds = img_array[::step, ::step]

        img_8bit = (img_ds >> 8).astype(np.uint8)
        img_rgb = np.stack([img_8bit]*3, axis=-1)
        img_rgb_t = np.transpose(img_rgb, (1, 0, 2))

        # faster top-left crop
        # return img_rgb_t[:self.target_width, :self.target_height, :]

        # central crop
        w, h = img_rgb_t.shape[:2]
        start_x = (w - self.target_width) // 2
        start_y = (h - self.target_height) // 2
        return img_rgb_t[start_x:start_x + self.target_width, start_y:start_y + self.target_height, :]

# we write the data to the file, and then update the surface with a little icon
# to denote that the save succeeded or failed. It is possible, for relatively
# fast exposures that the surface has already moved on and in those cases we
# should not update.
#
# A live writer should stop the python process from exiting, because the file
# must be written.
class FitsWriter:
    def __init__(self, data, out, surface, metadata):
        self.data = data
        self.out = out
        self.surface = surface
        self.metadata = metadata
        self.thread = threading.Thread(target=self.run, daemon=False, name=f"FitsWriter {out}")

    def start(self):
        self.thread.start()

    # marks the data as being stale. It must still be written to disk but icon
    # drawing should be skipped. This is probably going to be rethought with a
    # class holding the surface, and a filename based lock for doing all the
    # icon updates.
    def stale(self):
        pass

    def run(self):
        start = time.perf_counter()
        with fitsio.FITS(self.out, 'rw') as fits:
            fits.write(self.data, compress="rice")
            hdu = fits[-1]
            for k, v in self.metadata:
                hdu.write_key(k, v)
        end = time.perf_counter()
        elapsed = end - start
        print(f"FITS writing elapsed time: {elapsed:.4f} seconds")
        # TODO can we sync/flush to make sure it is written to silicon?

        # TODO if the write succeeded, and the surface is ok to write to then do
        # so. This is kinda fiddly and needs some thought.

class Menu:
    def __init__(self):
        self.capture = None
        self.mode = Mode.LIVE

    def update_settings(self, output_dir, camera, camera_settings, wheel, wheel_settings):
        assert not self.capture
        if not camera:
            return

        surface = pygame.display.get_surface()
        w = surface.get_width()
        h = surface.get_height()

        self.capture = Capture(w, h, output_dir, camera, camera_settings, wheel, wheel_settings, self.mode)
        self.capture.start()

    def cancel(self):
        if self.capture:
            self.capture.stop()
            self.capture = None

    def update(self, events):
        # FIXME a sub-mode selection menu
        surface = pygame.display.get_surface()

        if self.capture:
            bg = self.capture.get_latest()
            surface.blit(bg, (0, 0))
        else:
            # TODO filler "no camera"
            surface.fill((0, 0, 0))
