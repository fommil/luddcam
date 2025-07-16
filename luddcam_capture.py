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
# A zooms in, and pressing it again returns to the live view. This only impacts
# the view, it doesn't impact what is saved to disk. An icon is shown to
# indicate that it is zoomed in. In the future we may have a preview of the
# zoomed region, different zoom levels, and directional pad to move the region
# of interest (especially in preparation for planetary capture).
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
from enum import Enum
from pathlib import Path
import os
import re
import threading
import time

import fitsio
import numpy as np
import pygame

import pygame_menu

import luddcam_settings
from luddcam_settings import is_left, is_right, is_up, is_down, is_menu, is_start, is_action, is_back, is_button

ALIGN_LEFT=pygame_menu.locals.ALIGN_LEFT

class Mode(Enum):
    SINGLE = 0
    REPEAT = 1
    INTERVALS = 2

class Stage(Enum):
    LIVE = 0
    START = 1
    PAUSE = 2
    STOP = 3

class Capture:
    def __init__(self, view, output_dir, camera, camera_settings, wheel, wheel_settings):
        self.view = view
        self.output_dir = output_dir
        self.camera = camera
        self.camera_settings = camera_settings
        self.wheel = wheel
        self.wheel_settings = wheel_settings

        # variable that may be mutated by the UI threads
        self.lock = threading.Lock()
        self.mode = Mode.SINGLE
        self.stage = Stage.STOP
        self.interval_idx = None

        self.thread = threading.Thread(target=self.run, daemon=True, name="Capture")

        if output_dir:
            pattern = re.compile(r"IMG_(\d+)\.fits$")
            numbers = []
            for path in Path(self.output_dir).iterdir():
                match = pattern.match(path.name)
                if match:
                    numbers.append(int(match.group(1)))
            self.seq = max(numbers) + 1 if numbers else 1

    # Must be called by the UI thread to start the worker.
    def start(self):
        with self.lock:
            self.stage = Stage.LIVE
        return self.thread.start()

    # Can be called by the UI thread to change the Mode of operation.
    def set_mode(self, mode):
        with self.lock:
            # TODO should we force it to LIVE?
            self.mode = mode
            self.interval_idx = None

    # Can be called by the UI thread to change the Stage of the lifecycle.
    #
    # Stop will block the caller until the thread completes.
    def set_stage(self, stage):
        with self.lock:
            self.stage = stage
        if stage == Stage.STOP:
            self.thread.join()

    # Can be called by the UI thread to inspect the current Stage.
    def get_stage(self):
        with self.lock:
            return self.stage

    def run(self):
        # indicates that we started a capture using the given mode.
        # the remaining variable defined here are captured at the
        # point the capture started and may not match the latest.
        capturing = False
        capture_mode = None
        capture_stage = None
        capture_exposure = None
        capture_slot = None
        capture_interval_idx = None
        last_stage = None
        last_mode = None
        while True:
            time.sleep(0.1)
            with self.lock:
                # thread safe access
                mode = self.mode
                stage = self.stage
                interval_idx = self.interval_idx
            if last_stage != stage or last_mode != node:
                # TODO common state transition code (if LIVE is always forced
                # we can drop last_mode)
                pass
            last_stage = stage
            last_mode = mode

            if stage == Stage.STOP:
                self.camera.capture_stop()
                break
            if stage == Stage.PAUSE:
                if capturing:
                    capturing = False
                    capture_stage = None
                    self.camera.capture_stop()
                    self.view.paused()
                continue
            if stage == Stage.LIVE:
                if capturing and capture_stage is Stage.START:
                    capturing = False
                    capture_stage = None
                    self.camera.capture_stop()
                    continue

            if not capturing:
                capture_stage = stage
                capture_mode = mode

                if mode == Mode.INTERVALS:
                    intervals = self.camera_settings.intervals
                    if not interval_idx:
                        index, sub = (0, 0)
                    else:
                        index, sub = interval_idx
                    #print(f"last interval idx was {interval_idx}")
                    sub += 1
                    if sub >= intervals[index].frames:
                        index += 1
                        sub = 0
                        if index >= len(intervals):
                            print("repeating the interval plan")
                            index = 0
                    capture_interval_idx = (index, sub)
                    capture_exposure = intervals[index].exposure
                    if self.wheel:
                        capture_slot = intervals[index].slot
                        self.wheel.set_slot_and_wait(capture_slot)
                    # print(f"interval is starting {capture_interval_idx} with {capture_exposure} at {capture_slot} in plan {intervals[index]}")
                else:
                    # live, single, and repeat
                    capture_interval_idx = None
                    capture_exposure = self.camera_settings.exposure
                    if self.wheel:
                        capture_slot = self.wheel_settings.default
                        self.wheel.set_slot_and_wait(capture_slot)

                if stage == Stage.LIVE and capture_exposure > 1:
                    # intentionally limit live exposures
                    capture_exposure = 1

                self.camera.capture_start(capture_exposure)
                capturing = True
                continue

            # TODO guard the capture_wait calls for exposures > 1 sec.

            # we could guard this until it's nearer the time to expect an
            # exposure to be ready, if this impacts the camera negatively.
            status = self.camera.capture_wait()
            if status is False:
                continue
            elif status is None:
                capturing = False
                print("capture failed")
                self.view.no_signal()
                continue

            capturing = False
            print("capture complete")
            data = self.camera.capture_finish()
            if capture_stage == Stage.LIVE:
                self.view.set_data(None, data)
            elif not self.output_dir:
                self.view.set_data(False, data)
            else:
                out = f"{self.output_dir}/IMG_{self.seq:05}.fits"
                self.seq += 1
                self.view.set_data(out, data)

                if capture_mode == Mode.SINGLE:
                    # this allows the single image to stay on the screen until
                    # the user presses BACK to unpause or START to take another.
                    self.set_stage(Stage.PAUSE)
                elif capture_mode == Mode.INTERVALS:
                    # this allows us to track where we got to and if we paused
                    # during this exposure, it'll restart at exactly this point.
                    with self.lock:
                        self.interval_idx = capture_interval_idx

                if capture_slot is None:
                    filt = None
                else:
                    filt = self.wheel_settings.filters[capture_slot] or f"Slot {capture_slot + 1}"

                save_fits(out, self.view, capture_exposure, self.camera, filt, self.camera_settings.cooling)

        print(f"capture stopped for {self.camera.name}")

def save_fits(out, view, exp, camera, filt = None, cooling = None):
    print(f"...saving to {out}")

    # note that fitsio seems to automatically set BZERO and BSCALE
    metadata = []
    metadata.append(("PROGRAM", "luddcam"))
    metadata.append(("DATE", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")))
    metadata.append(("EXPTIME", exp))
    if filt:
        metadata.append(("FILTER", filt))

    metadata.append(("XPIXSZ", camera.pixelsize))
    metadata.append(("YPIXSZ", camera.pixelsize))
    metadata.append(("INSTRUME", camera.name))
    if (temp := camera.get_temp()) is not None:
        metadata.append(("CCD-TEMP", temp))
    if camera.is_cooled and cooling is not None:
        metadata.append(("SET-TEMP", cooling))
    if camera.gain != None:
        metadata.append(("GAIN", camera.gain))
    if camera.offset != None:
        metadata.append(("OFFSET", camera.offset))
    if camera.bayer:
        metadata.append(("BAYERPAT", camera.bayer))

    writer = FitsWriter(view, data, out, metadata)
    writer.start()

# we write the data to the file, and then update the view with a little icon
# to denote that the save succeeded or failed. It is possible, for relatively
# fast exposures that the surface has already moved on and in those cases we
# should not update.
#
# A live writer should stop the python process from exiting, because the file
# must be written.
class FitsWriter:
    def __init__(self, view, data, out, metadata):
        self.view = view
        self.data = data
        self.out = out
        self.metadata = metadata
        self.thread = threading.Thread(target=self.run, daemon=False, name=f"FitsWriter {out}")

    def start(self):
        self.thread.start()

    def run(self):
        start = time.perf_counter()
        with fitsio.FITS(self.out, "rw") as fits:
            fits.write(self.data, compress="rice")
            hdu = fits[-1]
            for k, v in self.metadata:
                hdu.write_key(k, v)
        end = time.perf_counter()
        elapsed = end - start
        # we're using removable media, let's flush our writes
        with open(self.out, "rb+") as f:
            os.fsync(f.fileno())
        print(f"FITS writing elapsed time: {elapsed:.4f} seconds")
        if self.view:
            self.view.saved(self.out, True)
        # TODO when the write fails

# Capture, and its spawned FitsWriter, will update the surface asynchronously
# (keyed by the file that identifies the capture). The main loop can call this
# to get the latest version.
#
# This is abstracted out so that it can be reused by guiding.
class View:
    def __init__(self, width, height):
        self.target_width = width
        self.target_height = height

        # only ever accessed on the UI thread
        self.surface = pygame.Surface((width, height))
        self.zoom = False

        # shared thread variables that must be accessed with a lock
        self.lock = threading.Lock()
        self.out = None
        self.img_raw = None
        self.img_rgb = None

    # Callable by the UI thread, enables or disables digital zoom.
    def toggle_zoom(self):
        with self.lock:
            self.img_rgb = None
            self.zoom = not self.zoom

    # thread safe way to write the surface out to the target, lazily
    # initialising all aspects of it (rendering is done on the calling thread).
    def blit(self, target):
        # grabs an atomic view of all the shared thread state
        with self.lock:
            out = self.out
            img_raw = self.img_raw
            img_rgb = self.img_rgb

        if img_raw is None:
            # TODO placeholder "waiting for capture"
            pass
        elif img_rgb is None:
            img_rgb = scale(img_raw, self.target_width, self.target_height, self.zoom)
            pygame.surfarray.blit_array(self.surface, img_rgb)
            # TODO stats and icons etc (input parameters)
            with self.lock:
                if img_raw is self.img_raw:
                    self.img_rgb = img_rgb

        target.blit(self.surface, (0, 0))

    def no_signal(self):
        # TODO placeholder image "no signal from camera"
        pass

    # the data designated for the given file.
    #
    # If out is False it indicates that the output dir was not set, and an error
    # should be displayed on the image. If it is None it means the image will
    # not be updated any further (e.g. live).
    def set_data(self, out, img_raw):
        with self.lock:
            self.out = out
            self.img_raw = img_raw
            self.img_rgb = None

    def paused(self):
        # TODO implement
        pass

    # the file writer indicates a file was (or wasn't) written to disk
    def saved(self, out, success):
        # TODO implement
        # with self.lock:
        pass

class Menu:
    def __init__(self, output_dir, camera, camera_settings, wheel, wheel_settings):
        surface = pygame.display.get_surface()
        w = surface.get_width()
        h = surface.get_height()

        self.view = View(w, h)
        if not camera:
            self.capture = None
            self.view.no_signal()
            self.menu = None
            return

        self.capture = Capture(self.view, output_dir, camera, camera_settings, wheel, wheel_settings)
        self.capture.start()

        self.menu = luddcam_settings.mk_menu("Capture")
        self.menu_active = False

        def select_mode(a, mode):
            print(f"setting mode to {mode}")
            self.capture.set_mode(mode)

        items = [("Single", Mode.SINGLE), ("Repeat", Mode.REPEAT)]
        if camera_settings.intervals:
            items.append(("Intervals", Mode.INTERVALS))
        # TODO persist in the session which Mode is default, so going into the
        # settings doesn't erase the choice.
        self.menu.add.selector(
            "Mode: ",
            items=items,
            default=0,
            onchange=select_mode,
            align=ALIGN_LEFT)

    def cancel(self):
        if self.capture:
            self.capture.set_stage(Stage.STOP)
            self.capture = None

    def update(self, events):
        if not self.capture:
            self.view.blit(screen)
            return

        screen = pygame.display.get_surface()
        if self.menu_active:
            for event in events:
                if is_action(event) or is_back(event):
                    self.menu_active = False

            self.menu.update(events)
            self.menu.draw(screen)
            return

        for event in events:
            if is_left(event):
                # we'll leave the active stage running
                self.menu_active = True
            elif is_start(event):
                if self.capture.get_stage() == Stage.START:
                    self.capture.set_stage(Stage.PAUSE)
                else:
                    self.capture.set_stage(Stage.START)
            elif is_action(event):
                print("TOGGLE ZOOM")
                self.view.toggle_zoom()
            # TODO back should put LIVE into PAUSE, or we should have a method
            # called "defocus" or something that achieves the same thing. Then
            # when update is called and the defocussed flag was set we can turn
            # PAUSE into LIVE. And similar for guiding.

        self.view.blit(screen)

# TODO write some tests for the screen rendering, use DSLR fits files to test
# rendering RGB data.

# TODO support 8 bit mono and bayered RGB data
#
# zoom means to crop to the target rather than downscale.
#
# also, does this work if the target is larger than the original?
def scale(mono, target_width, target_height, zoom):
    height, width = mono.shape

    if zoom:
        startx = width//2 - target_width//2
        starty = height//2 - target_height//2
        print(f"rendering zoomed version ({startx}, {starty}, {target_width}, {target_height})")
        img_ds = mono[starty:starty + target_height,startx:startx + target_width]
    else:
        scale_w = width / target_width
        scale_h = height / target_height
        scale = min(scale_w, scale_h)
        step = max(1, int(scale))
        img_ds = mono[::step, ::step]

    # TODO autostretch instead of dropping the lower bits
    img_8bit = (img_ds >> 8).astype(np.uint8)
    img_rgb = np.stack([img_8bit]*3, axis=-1)
    img_rgb_t = np.transpose(img_rgb, (1, 0, 2))

    # faster top-left crop
    # return img_rgb_t[:target_width, :target_height, :]

    # center crop
    w, h = img_rgb_t.shape[:2]
    start_x = (w - target_width) // 2
    start_y = (h - target_height) // 2
    return img_rgb_t[start_x:start_x + target_width, start_y:start_y + target_height, :]
