# This reuses a lot of the same code as the capture, but everything is subtely
# different enough that it is easier and cleaner to copy/paste chunks of code
# instead of pushing the logic into abstract layers.
#
# The primary purpose of this code is to take short exposures with the guide
# camera and send instructions to the mount, in order to retain the same
# alignment throughout the . In the future we may wish to coordinate the guiding
# and capture so that we can add dithering, to even out pattern noise.
#
# Most logic happens in the background without user interaction. Within this mode
# the user can select:
#
# A to zoom in or out, intended to assist with prime focus.
#
# START to begin the calibration process.
#
# Otherwise a live view is always visible with various metadata including
# circles around the primary stars that are used for guiding, and basic stats
# about status, FWHM and a quality indicator.
#
# Everything is intended to be automatic, so the algorithm may choose to change
# the exposure time if it feels it would benefit from it.

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import os
import re
import random
import string
import threading
import time

import fitsio
import numpy as np
import pygame

import pygame_menu

import luddcam_astrometry
import luddcam_settings
from luddcam_images import *
from luddcam_settings import is_left, is_right, is_up, is_down, is_menu, is_start, is_action, is_back, is_button
import mocks

class Stage(Enum):
    LIVE = 0
    START = 1
    PAUSE = 2
    STOP = 3

# FIXME calibration

# FIXME extract usefulness out of this brain dump...
#
# I already have subpixel sources. I ended up caving in and just using
# this library (implementing the SExtractor logic) because it is zero
# dependency and works very well although it could be a bit more efficient
# by releasing the python global interpreter lock when calling out to
# native code, so it blocks the whole app while it is running (thankfully
# usually only a tenth of a second on a raspberry pi):
#
#   https://sep.readthedocs.io/en/stable/
#
# If you recall one of my first ideas was to fit PSFs and I was pleased to
# see that it is indeed an option in sep. There's a few options to explore
# there in terms of kernel shapes, although I'd need to figure out the
# scale of the PSF for the current seeing conditions
#
# https://github.com/sep-developers/sep/blob/main/sep.pyx#L606
#
# That said, for plate solving it was totally fine to skip the convolution
# step entirely (no kernel), and I presume this means it falls back to the
# multi-scale neural network. There is the option to do a follow up to get
# more accurate object centroids using a "windowed" algorithm, but it's
# not clear to me at this stage if that is necessary, but for sure this
# would allow taking a closer look at the guide stars.
#
# Then we have to understand how the night sky has moved between two
# frames. How are you doing that in phd2? I've got a couple of ideas here,
# the absolute simplest idea is to do a nearest neighbour search for each
# source (within an expected flux and maximum distance), then take the
# median distance moved relative the calibrated axes. I'm hoping that
# should take general atmospheric wobble into account. Do you think that
# will be good enough, is there anything more advanced I should be looking
# at?
#
# Then there's a big trade off to make: should we do RA/DEC movements one
# at a time or both at the same time? The advantage of one at a time is
# that we can continue to keep our a priori knowledge about the movement
# vectors up to date, including refining backlash estimates. But it's
# slower, which potentially results in less accurrate guiding over the
# long run. Is that something you leave as a user option or did you lock
# it down by design? Maybe I can mix and match, to go through periods of
# time where feedback is being collected.
#
# The next trade off is: how far do we move? We have a running estimate,
# initially populated by the calibration data (and further complicated by
# backlash). I presume this is what you encode with the "aggression"
# parameter (or at least that's how it is exposed in the asiair). I was
# thinking about putting a simple optimiser on this by correlating the
# parameter against the actual under / overshoot.
#
# When it comes to backlash, have you noticed that it follows a particular
# distribution? (ignoring dodgy gears) I'm thinking about gear geometry
# and it's hard to really guess anything. I've only got two mounts to test
# on: one has the Rowan drive and the other is just the classic two gears
# with backlash but there's no way to measure anything to any level of
# accuracy without clear skies. Luckily, backlash is a very well studied
# subject from robotics so I probably just need to hit the books here.
#
# It is common to let the user decide the exposure length and camera gain.
# I'm actually thinking about taking that out of their hands and doing it
# based on getting high enough quality source maps. But I have no idea
# what "high enough quality" looks like in the general case. Avoiding
# saturation is the only obvious thing that comes to mind (named stars can
# absolutely do this). Do you have any thoughts on what a "good" source
# map looks like? SEP gives me access to lots of stats about the flux that
# I could use here.


# hot pixels (median filter?)
# custom psf

class Guide:
    def __init__(self, view, output_dir, guide):
        self.view = view
        rdm = ''.join(random.choices(string.ascii_letters, k=8))
        self.debug_dir = f"{output_dir}/guiding/{rdm}/"
        print(f"setting guiding debug directory to {self.debug_dir}")
        self.guide = guide
        self.seq = 0

        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True, name="Guide")
        self.stage = Stage.STOP

    # Must be called by the UI thread to start the worker.
    def start(self):
        with self.lock:
            self.stage = Stage.LIVE
        return self.thread.start()

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

    # Called by the UI thread to initially align the st4 movements with vectors
    # relative to a typical capture in this session. The vectors should be
    # recoverable at any time so that we can create a fresh instance if the
    # guide settings have not changed, avoiding the need for another
    # calibration.
    def calibrate(self):
        with self.lock:
            # TODO implement calibration
            pass

    def run(self):
        capturing = False
        capture_exposure = None
        capture_stage = None
        last_stage = None
        while True:
            time.sleep(0.1 / mocks.warp)
            with self.lock:
                stage = self.stage
            if last_stage is not stage:
                # TODO common state transition code
                pass
            last_stage = stage

            if stage == Stage.STOP:
                self.guide.capture_stop()
                break
            if stage == Stage.PAUSE:
                if capturing:
                    capturing = False
                    capture_stage = None
                    self.guide.capture_stop()
                    self.view.paused()
                continue
            if stage == Stage.LIVE:
                if capturing and capture_stage is Stage.START:
                    capturing = False
                    capture_stage = None
                    self.guide.capture_stop()
                    continue

            if not capturing:
                capture_stage = stage
                capture_exposure = 2 # TODO auto or user guide exposures

                if stage == Stage.LIVE:
                    # intentionally limit live exposures
                    capture_exposure = 1

                self.guide.capture_start(capture_exposure)
                capturing = True
                continue

            # TODO guard capture_wait with a timer

            # we could guard this until it's nearer the time to expect an
            # exposure to be ready, if this impacts the camera negatively.
            status = self.guide.capture_wait()
            if status is False:
                continue
            elif status is None:
                capturing = False
                print("guide capture failed")
                self.view.no_signal()
                continue

            capturing = False
            print("guide capture complete")
            data = self.guide.capture_finish()
            metadata = mk_metadata(capture_exposure, self.guide, None, None, None)
            if capture_stage == Stage.LIVE:
                self.view.set_data(None, data)
            else:
                out = f"{self.debug_dir}/IMG_{self.seq:05}.fit"
                self.seq += 1

                self.view.set_data(out, data)

                # eventually this will only be when debugging is enabled
                save_fits(out, self.view, data, metadata)

                # TODO do the guiding calculations here and send corrections

        print(f"guide capture stopped for {self.guide.name}")

class Menu:
    def __init__(self, output_dir, guide):
        surface = pygame.display.get_surface()
        w = surface.get_width()
        h = surface.get_height()

        # this is a bit of a hack to reuse the view, guiding needs its own view
        self.view = View(w, h)

        if not guide:
            self.guide = None
            self.view.message("no guide camera")
            self.menu = None
            return

        self.guide = Guide(self.view, output_dir, guide)
        self.guide.start()

        self.menu = luddcam_settings.mk_menu("Guide")
        self.menu_active = False

        # TODO add menu to enable calibration and start the guiding

    def cancel(self):
        if self.guide:
            self.guide.set_stage(Stage.STOP)
            self.guide = None

    def update(self, events):
        screen = pygame.display.get_surface()

        if not self.guide:
            self.view.blit(screen)
            return

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
                    self.capture.set_stage(Stage.LIVE)
                else:
                    self.capture.set_stage(Stage.START)
            elif is_action(event):
                print("TOGGLE GUIDE ZOOM")
                self.view.toggle_zoom()

        self.view.blit(screen)

if __name__ == "__main__":
    import sep

    f = "tmp/guiding/Light_FOV_3.0s_Bin1_20250921-220306_0053.fit.fz"

    img_raw, headers = load_fits(f)
    img_raw = (img_raw >> 4) # undoes asiair bullshit
    bayer = get_corrected_bayer(headers)

    bit_depth = 12 #headers.get("BITDEPTH", img_raw.dtype.itemsize * 8)
    print(f"bit depth = {bit_depth}, dtype = {img_raw.dtype}")

    threshold = (1 << bit_depth) - 1  # high bit for given depth
    print(f"using threshold {threshold}, min = {np.min(img_raw)}, max = {np.max(img_raw)}")
    # excludes hot pixels AND stars that risk saturation
    ys, xs = np.where(img_raw >= threshold * 0.9)
    hot_pixels = list(zip(xs.tolist(), ys.tolist()))

    print(f"{len(hot_pixels)} hot pixels")

    data = img_raw.astype(np.float32)
    objs = luddcam_astrometry.source_extract(data, cull = None)

    # for guide star selection we want to exclude anything that is impacted by
    # saturation (including hot pixels), anything with outlier 'a/b' (galaxies
    # and nebulae), or anything that is within 2*a of any other target (using
    # the a of both targets). Then capped to a sensible amount.

    guides = []
    disqualified = []
    for o in objs:
        # FIXME remove anything near a hot pixel
        x = float(o['x'])
        y = float(o['y'])
        a = float(o['a'])
        for hot_x, hot_y in hot_pixels:
            if x - a <= hot_x <= x + a and y - a <= hot_y <= y + a:
                print(f"GOT A HOT ONE! {(x, y, a)} near {(hot_x, hot_y)} (flux is {o['flux']})")
                break


    # print(objs['b'] / objs['a'])

    exit(0)
    #print(np.median(objs['a']))

    # winpos is supposed to improve centroid estimation but the docs requiring a
    # ridiculous about of photometric analysis to estimate the sigma for each
    # source, see https://sep.readthedocs.io/en/stable/apertures.html instead I
    # would like to do consider a simple approximation of using half the
    # semi-major axis. The actual sigma value has a huge influence on the
    # correction.
    #
    # to test if winpos actually improves things (and what scaling factor hack
    # to apply to the semi-major), we need to compute the stddev of the movement
    # difference between two frames since we have no ground truth for the actual
    # centers. We do have a sort-of ground truth, in the plate solver, so could
    # also look for plate solution stability. Unfortunately there's no way to get
    # the error to the ground truth from astrometry.
    #
    # I am still suspicious that this is needed, it feels like with
    # enough guide stars it should all average out.
    for factor in range(150):
        sig = objs['a'] * factor / 100
        start = time.perf_counter()
        xwin, ywin, flag = sep.winpos(data, objs['x'], objs['y'], sig)
        end = time.perf_counter()
        #print(f"better centroids took {end - start}")

        xdiff = objs['x'] - xwin
        ydiff = objs['y'] - ywin

        print(f"sig = {factor / 100} median corrections = {np.median(np.abs(xdiff)):.2f}, {np.median(np.abs(ydiff)):.2f}")
    #print(centroids)


    #pygame.image.save(surface, "test.png")

    #subprocess.Popen(["feh", "--force-aliasing", "test.png"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Local Variables:
# compile-command: "python3 luddcam_guide.py"
# End:
