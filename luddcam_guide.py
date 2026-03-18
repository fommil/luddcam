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

from enum import Enum
import glob
import random
import string
import threading
import time

import numpy as np
import pygame


import luddcam_astrometry
import luddcam_settings
from luddcam_images import *
from luddcam_settings import is_left, is_start, is_action, is_back
import mocks

class Stage(Enum):
    LIVE = 0
    START = 1
    PAUSE = 2
    STOP = 3

# TODO calibration

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

def find_guide_stars(img_raw, bit_depth, min_dist):
    threshold = (1 << bit_depth) - 1  # high bit for given depth
    #print(f"using threshold {threshold}, min = {np.min(img_raw)}, max = {np.max(img_raw)}")
    # excludes hot pixels AND stars that risk saturation
    ys, xs = np.where(img_raw >= threshold * 0.9)
    hot_pixels = list(zip(xs.tolist(), ys.tolist()))

    def near_hot_pixel(x, y, a, scale=2.0, min_dist=10):
        a = max(a * scale, min_dist)
        for hot_x, hot_y in hot_pixels:
            if x - a <= hot_x <= x + a and y - a <= hot_y <= y + a:
                return True

    #print(f"{len(hot_pixels)} hot pixels")

    data = img_raw.astype(np.float32)

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
    objs = luddcam_astrometry.source_extract(data, cull = 50, windowed_improvements = True)
    # TODO cull size seems to have a big impact on stability of the diff

    # for guide star selection we want to exclude anything that is impacted by
    # saturation (including hot pixels), anything with outlier 'a/b' (galaxies
    # and nebulae), or anything that is within 2*a of any other target (using
    # the a of both targets). Then capped to a sensible amount.

    guides = np.empty(0, dtype=objs.dtype)
    def near_existing_guide(x, y, a, scale=2.0, min_dist=10.0):
        gx = guides['x']
        gy = guides['y']
        a_scaled = max(scale * a, min_dist)
        ga_scaled = np.maximum(scale * guides['a'], min_dist)
        x_near = (np.abs(gx - x) <= a_scaled) | (np.abs(gx - x) <= ga_scaled)
        y_near = (np.abs(gy - y) <= a_scaled) | (np.abs(gy - y) <= ga_scaled)
        return np.any(x_near & y_near)

    for o in objs:
        x = float(o['x'])
        y = float(o['y'])
        a = float(o['a'])

        if near_hot_pixel(x, y, a, scale=10):
            continue
        if near_existing_guide(x, y, a, scale=10, min_dist=min_dist):
            continue

        guides = np.append(guides, o)

    return guides

def find_guide_diff(guide1, guide2, search_box):
    diffs = []
    dd = search_box
    for g1 in guide1:
        x1 = float(g1['x'])
        y1 = float(g1['y'])
        a1 = float(g1['a'])
        f1 = float(g1['flux'])
        for g2 in guide2:
            x2 = float(g2['x'])
            y2 = float(g2['y'])
            a2 = float(g2['a'])
            f2 = float(g2['flux'])

            if x1 - dd <= x2 <= x1 + dd and y1 - dd <= y2 <= y1 + dd:
                if not 0.8 < a1/a2 < 1.2:
                    # print(f"warning size changed from {a1} ({f1}) to {a2} ({f2})") # how
                    break

                diffs.append((x1 - x2, y1 - y2))
                break

    if not diffs:
        return None

    return tuple(float(x) for x in np.median(np.array(diffs), axis=0))

if __name__ == "__main__":
    bit_depth = 12 #headers.get("BITDEPTH", img_raw.dtype.itemsize * 8)
    d = 50

    all_diffs = []

    ref_frame = None
    i = 0

    # FIXME implement guiding
    for f in sorted(glob.glob("tmp/guiding/*.fit.fz")):
        i = i + 1
        if i > 100:
            break
        img, _ = load_fits(f, 4) # was saved with the zwo bugs

        frame = find_guide_stars(img, bit_depth, d)

        if ref_frame is None:
            ref_frame = frame
        else:
            diff = find_guide_diff(ref_frame, frame, 0.75 * d)
            if diff is None:
                # if this happens too many times in a row or gets too big, we
                # should consider resetting the reference frame.
                print("failed to find the diff")
            elif max(abs(x) for x in diff) > 5:
                # epic fail, camera must have been knocked or repointed. This
                # realistically needs a recalibration due to rotation induced by
                # sag or bad alignment, we could theoretically account for that
                # by looking for systemic biases in our corrections or
                # estimating the misalignment, but that's not the luddite way.
                print("RESET")
                ref_frame = frame
            else:
                all_diffs.append(diff)
                print(diff)

    deltas = np.array(all_diffs)

    # use a reference frame, not frame by frame deltas but that means we need to
    # think about when to reset. But we need to think about when to reset the
    # frame.

    # instead of doing a cum sum we could also
    # use a fixed reference frame
    #pos = np.cumsum(deltas, axis=0)

    rms_dx = np.sqrt(np.mean(deltas[:,0]**2))
    rms_dy = np.sqrt(np.mean(deltas[:,1]**2))

    print(f"RMS = {rms_dx}, {rms_dy}")

# Local Variables:
# compile-command: "python3 luddcam_guide.py"
# End:
