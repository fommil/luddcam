# The live / capture mode. When this mode is selected a Thread is spawned which
# continually updates (with minimum/maximum exposures) a buffer that is
# (optionally) displayed on the pygame surface by the main UI thread, and/or
# writes to disk.
#
# This file contains both the exposure thread, and the code (that runs on the
# main UI thread) to produce the menu.
#
# A design choice is that the settings are only accessible through the SELECT
# menu, we're not providing sub-menus. This keeps everything nice and simple.
#
# Discussion here about Siril header requirements:
# https://discuss.pixls.us/t/formats-and-headers-supported-required-by-siril/50531
#
# Polar Alignment works like this: imagine you have a print out of concentric
# circles and a transparent sheet with the same circles. If perfectly aligned,
# they will overlap. To simulate bad polar alignment, move the transparent sheet
# a little and find a point on one of the circles where the lines cross. Then
# travel 90 degrees in either direction, this is the drift over 6 hours and can
# be corrected by moving the transparent sheet by the misalignment amount. Note
# that this assumes that only the azimuth direction is misaligned. To handle the
# more likely scenario that is a mixture of altitude and azimuth, we need to
# take a heuristic approach: pick any point in the sky (we can visualise this by
# drawing a circle on the transparent sheet so that it crosses the paper's
# circle at exactly the point of the sky we're looking at). Then move any
# distance from 30 to 90 degrees and note the difference. Find the midpoint
# between where we are and where we should be, and have the user realign their
# telescope (using only alt/az screws) to point there. Repeating gets
# increasingly closer to the true alignment and we can even optimise this
# algorithm when the movement is entirely in the alt or az direction (which
# requires location and time information, that luddcam can't access... but the
# user can do this manually when they notice that a single screw is doing the
# heavy lifting).

from datetime import datetime, timezone
from enum import Enum
from fractions import Fraction
from pathlib import Path
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import time

import numpy as np
import pygame

import pygame_menu

import luddcam_astrometry
import luddcam_settings
from luddcam_settings import is_back, is_left, is_right, is_up, is_down, is_start, is_action, is_button
import mocks

from luddcam_images import *

ALIGN_LEFT=pygame_menu.locals.ALIGN_LEFT

class Mode(Enum):
    SINGLE = 0
    REPEAT = 1
    INTERVALS = 2

class Stage(Enum):
    LIVE = 0 # actively capturing data in a fixed loop and reduced exposure
    CAPTURE = 1 # actively capturing data with the user's settings
    PAUSE = 2 # not capturing data, showing last capture on screen
    STOP = 3 # shutting down

class Capture:
    def __init__(self, view, output_dir, camera, camera_settings, wheel, wheel_settings, mode):
        self.view = view
        self.output_dir = output_dir
        self.camera = camera
        self.camera_settings = camera_settings
        self.wheel = wheel
        self.wheel_settings = wheel_settings

        # variable that may be mutated by the UI threads
        self.lock = threading.Lock()
        self.mode = mode
        self.stage = Stage.STOP
        self.interval_idx = None

        self.live_cap = 1

        self.thread = threading.Thread(target=self.run, daemon=True, name="Capture")

        if output_dir:
            pattern = re.compile(r"IMG_(\d+)\.fit.*$")
            numbers = []
            for path in Path(self.output_dir).iterdir():
                match = pattern.match(path.name)
                if match:
                    numbers.append(int(match.group(1)))
            self.seq = max(numbers) + 1 if numbers else 1

    # Must be called by the UI thread to start the worker.
    def start(self):
        self.set_stage(Stage.LIVE)
        return self.thread.start()

    # Can be called by the UI thread to change the Mode of operation.
    def set_mode(self, mode):
        with self.lock:
            self.mode = mode
            self.interval_idx = None
            self.image_count = 0
        self.view.message(f"{mode.name} selected")

    # Can be called by the UI thread to change the Stage of the lifecycle.
    #
    # Stop will block the caller until the thread completes.
    def set_stage(self, stage):
        if stage is Stage.CAPTURE and not self.output_dir:
            self.view.message("drive not selected")
            return
        with self.lock:
            self.stage = stage
            self.image_count = 0
        if stage is Stage.STOP:
            self.thread.join()
        if stage is Stage.CAPTURE or stage is Stage.LIVE:
            self.view.message("initialising")
        if stage is Stage.PAUSE:
            self.view.pause(True)
        else:
            self.view.pause(False)

    # Can be called by the UI thread to inspect the current Stage.
    def get_stage(self):
        with self.lock:
            return self.stage

    # Can be called by the UI thread to inspect the current Mode.
    def get_mode(self):
        with self.lock:
            return self.mode

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
        capture_start = None
        capture_end = None
        capture_target = None
        last_stage = None
        last_mode = None
        while True:
            time.sleep(0.1 / mocks.warp)
            with self.lock:
                # thread safe access
                mode = self.mode
                stage = self.stage
                interval_idx = self.interval_idx
                image_count = self.image_count
            if last_stage is not stage or last_mode is not mode:
                # TODO common state transition code (if LIVE is always forced
                # we can drop last_mode)
                pass
            last_stage = stage
            last_mode = mode

            if stage is Stage.STOP:
                self.camera.capture_stop()
                break
            if stage is Stage.PAUSE:
                if capturing:
                    capturing = False
                    capture_stage = None
                    self.camera.capture_stop()
                continue
            if stage is Stage.LIVE:
                if capturing and capture_stage is Stage.CAPTURE:
                    capturing = False
                    capture_stage = None
                    self.camera.capture_stop()
                    continue

            if not capturing:
                capture_stage = stage
                capture_mode = mode

                if mode is Mode.INTERVALS:
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

                if stage is Stage.LIVE and capture_exposure > self.live_cap:
                    # intentionally limit live exposures
                    capture_exposure = self.live_cap

                capture_start = time.monotonic()
                # print(f"starting exposure with {capture_exposure} at {capture_start}")
                # if capture_end:
                #     print(f".... we waited {capture_start - capture_end:.2f} secs between captures")

                # we could reuse capturing here but it's cleaner to introduce a separate variable
                capture_target = time.monotonic() + capture_exposure / mocks.warp

                self.camera.capture_start(capture_exposure)
                capturing = True

                if image_count == 0:
                    if self.stage is Stage.LIVE:
                        summary = f"LIVE ({capture_exposure}s)"
                    elif self.mode is Mode.SINGLE:
                        summary = f"SINGLE ({capture_exposure}s)"
                    elif self.mode is Mode.REPEAT:
                        summary = f"REPEAT ({capture_exposure}s)"
                    else:
                        summary = f"INTERVAL ({len(intervals)} steps)"
                    self.view.message(summary)
                with self.lock:
                    self.image_count += 1

                continue

            # to help save on battery we just park it here. This means reaction
            # time (e.g. if the user asks to cancel) will be compromised but so
            # long as we don't go crazy it's ok. It's also good to ping the
            # camera regularly to ensure no part of the connection sleeps.
            if capture_target:
                remaining = (capture_target - time.monotonic() - 0.1)
                if remaining > 0:
                    park = min(5 / mocks.warp, remaining)
                    # print(f"PARKING for {park}")
                    time.sleep(park)

            # exposure to be ready, if this impacts the camera negatively.
            status = self.camera.capture_wait()
            if status is False:
                continue
            elif status is None:
                capturing = False
                # print("capture failed")
                self.view.message("no signal")
                continue

            capturing = False
            capture_end = time.monotonic()
            # print(f"capture complete ({capture_end - capture_start:.2f} secs), now {capture_end}")
            data = self.camera.capture_finish()
            assert(data is not None)
            if capture_slot is None:
                filt = None
            else:
                filt = self.wheel_settings.filters[capture_slot] or f"Slot {capture_slot + 1}"
            metadata = mk_metadata(capture_exposure, self.camera, filt, self.camera_settings.cooling, self.view.get_plate())
            # some extra info for the view, might go unused but it could be useful
            interval_info = ""
            if capture_interval_idx:
                index, sub = capture_interval_idx
                steps = len(self.camera_settings.intervals)
                if stage is Stage.LIVE:
                    interval_info = f"{steps} steps"
                else:
                    interval_info = f"{sub}|{index + 1}|{steps}"
            metadata_ = metadata + [("MODE", capture_mode),
                                    ("STAGE", capture_stage),
                                    ("IMAGE_COUNT", image_count),
                                    ("SINGLE_EXPTIME", self.camera_settings.exposure),
                                    ("INTERVAL_INFO", interval_info)]
            if capture_stage is Stage.LIVE:
                self.view.set_data(None, data, metadata_)
            elif not self.output_dir:
                self.view.set_data(False, data, metadata_)
            else:
                out = f"{self.output_dir}/IMG_{self.seq:05}.fit"
                self.seq += 1
                self.view.set_data(out, data, metadata_)

                if capture_mode is Mode.SINGLE:
                    # this allows the single image to stay on the screen until
                    # the user presses A or B to unpause or CAPTURE to take another.
                    self.set_stage(Stage.PAUSE)
                elif capture_mode is Mode.INTERVALS:
                    # this allows us to track where we got to and if we paused
                    # during this exposure, it'll restart at exactly this point.
                    with self.lock:
                        self.interval_idx = capture_interval_idx

                save_fits(out, self.view, data, metadata)

        print(f"capture stopped for {self.camera.name}")

# Capture, and its spawned FitsWriter, will update the surface asynchronously
# (keyed by the file that identifies the capture). The main loop can call this
# to get the latest version.
#
# This is abstracted out so that it can be reused by guiding.
class View:
    def __init__(self, width, height):
        self.target_width = width
        self.target_height = height

        # only ever accessed from the UI thread
        self.surface = pygame.Surface((width, height))
        self.zoom = False

        # shared thread variables that must be accessed with a lock
        # this is mostly for visibility, rather than avoiiing races
        self.lock = threading.Lock()
        self.out = None
        self.img_raw = None
        self.stale = True
        self.meta = None
        self.is_paused = False
        self.saved = False

        self.plate_solve = False
        self.align = None # ra, dec for the base then ra, dec for the test
        self.hints = SolverHints()

        self.font_large = pygame.font.Font(luddcam_settings.hack, 32)
        self.font_small = pygame.font.Font(luddcam_settings.hack, 14)

    def set_plate_solve(self, val):
        with self.lock:
            self.plate_solve = val

    def get_plate_solve(self):
        with self.lock:
            return self.plate_solve

    def toggle_align(self):
        with self.lock:
            hint_ra = self.hints.ra_center
            hint_dec = self.hints.dec_center
            if hint_ra is None or hint_dec is None:
                # fail, reset
                self.align = None
                return
            pos = (hint_ra, hint_dec)
            match self.align:
                case None:
                    # start
                    self.align = (pos, None)
                case (start, None) :
                    # prompt to realign
                    self.align = (start, pos)
                case _:
                    # done
                    self.align = None

    def get_plate(self):
        with self.lock:
            return self.hints

    # Callable by the UI thread, enables or disables digital zoom.
    def toggle_zoom(self):
        with self.lock:
            self.stale = True
            self.zoom = not self.zoom
            return self.zoom

    def disable_zoom(self):
        with self.lock:
            if self.zoom:
                self.stale = True
            self.zoom = False
            return self.zoom

    # thread safe way to write the surface out to the target, lazily
    # initialising all aspects of it (rendering is done on the calling thread).
    def blit(self, target):
        self.render()
        target.blit(self.surface, (0, 0))

    def render(self):
        # print(f"requested to render the image lazily...(zoom={self.zoom})")
        # grabs an atomic view of all the shared thread state
        with self.lock:
            out = self.out
            img_raw = self.img_raw
            stale = self.stale
            meta = self.meta
            surface = self.surface
            zoom = self.zoom
            is_paused = self.is_paused
            saved = self.saved
            plate_solve = self.plate_solve
            hints = self.hints if plate_solve else None
            align = self.align
            # doing it here instead of after avoids many timing issues
            self.stale = False
        if not stale:
            return

        if isinstance(img_raw, str) or img_raw is None:
            surface.fill(BLACK)
            if img_raw:
                text = self.font_large.render(img_raw, True, WHITE)
                rect = text.get_rect(center=(surface.get_width()//2, surface.get_height()//2))
                surface.blit(text, rect)
        else:
            is_saved = saved == out
            render_frame_for_screen(surface, img_raw, zoom, meta, out, self.font_small, is_paused, is_saved, hints, align)

    def message(self, msg):
        self.set_data(None, msg, None)

    # the data designated for the given file.
    #
    # If out is False it indicates that the output dir was not set, and an error
    # should be displayed on the image. If it is None it means the image will
    # not be updated any further (e.g. live).
    def set_data(self, out, img_raw, meta):
        with self.lock:
            # print(f"set_data with {meta}")
            self.out = out
            self.img_raw = img_raw
            self.meta = dict(meta) if meta else None
            self.stale = True

    # visually indicates that we are not exposing at present
    def pause(self, value):
        with self.lock:
            self.is_paused = value
            self.stale = True

    # the file writer indicates a file was written to disk
    def save(self, out):
        with self.lock:
            # print(f"SAVED = {out}")
            self.saved = out
            self.stale = True

class SolverHints:
    def __init__(self):
        self.ra_center = None
        self.dec_center = None
        self.pixscale = None # of the image that was platesolved
        self.parity = None
        self.focal_length = None

# moved out for easier manual testing. Mutates the solver_hints
# on a successful plate solve.
def render_frame_for_screen(surface, img_raw, zoom, meta, out, font, paused, saved, solver_hints, polar_align):
    raw_height, raw_width = img_raw.shape
    target_width, target_height = surface.get_size()

    mode = meta["MODE"]
    stage = meta["STAGE"]

    solve = solver_hints is not None and not zoom and ((mode == Mode.SINGLE and saved) or stage == Stage.LIVE)
    polar_align = polar_align if solve and stage == Stage.LIVE else False

    start = time.perf_counter()
    bayer = meta.get("BAYERPAT")
    img_rgb, img_mono = downscale(img_raw, target_width, target_height, zoom, bayer)
    height, width, _ = img_rgb.shape
    end = time.perf_counter()
    print(f"downscale took {end - start}")

    # plate solving is relatively expensive and it might seem silly to do it
    # here and block this thread, but it is the only way to do it in a way
    # that looks good to the user. We could consider doing a basic centroid
    # conversion and reuse the last solution to save some cycles, or look
    # into having a hot server wrapper over solve-field if it's a problem.
    centroids = []
    solved, relevant_stars, relevant_dsos, polar_alignment_points, polar_alignment_targets = False, None, None, None, None
    if solve:
        # we throw away data in pairs of two, which effectively means picking
        # the first colour in the bayer for OSC. We also take the opportunity to
        # downscale big (full frame) images that are overkill.
        img_mono = pixel_sample(img_raw, min(raw_width // 2, 2000), min(raw_height // 2, 2000)).astype(np.float32)
        # relative to displayed image, for solving
        scale_factor_v = img_mono.shape[0] / height
        # print(f"factors = {scale_factor}, {scale_factor_v}")
        centroids = luddcam_astrometry.source_extract(img_mono)
        x = centroids["x"] / scale_factor_v
        y = centroids["y"] / scale_factor_v
        flux = centroids["flux"]
        # scaled centroids relative to original (for focal length calc)
        scale_factor = raw_height / height

        centroids = np.array(list(zip(x, y, flux)), dtype=[("x", float), ("y", float), ("flux", float)])
        solved, relevant_stars, relevant_dsos, polar_alignment_points, polar_alignment_targets = plate_solve(solver_hints, centroids, width, height, scale_factor, meta.get("XPIXSZ"), polar_align)

    focus_magic = None
    if zoom and stage == Stage.LIVE and img_mono is not None:
        # we could calculate focus magic on the entire image, but that can be
        # slow and introduce frustrating lag. Therefore we use the zoomed region
        # which has been carefully prepared for us already. Note that the
        # difference in the numbers can be quite substantial, so maybe revisit.
        start = time.perf_counter()
        centroids = luddcam_astrometry.source_extract(img_mono.astype(np.float32))
        end = time.perf_counter()
        print(f"focus magic took {end - start}")
        #focus_magic = np.median(centroids["a"])
        focus_magic = np.average(centroids["a"], weights=centroids["flux"])

    img_rgb = quantize(img_rgb, meta.get("EXPTIME", 0) >= 1)

    # pygame expects (w,h,3) but everything else is (h,w,3)
    img_rgb = np.transpose(img_rgb, (1, 0, 2))
    # prefer this for the image and any plate solving markup
    img_surface = pygame.surfarray.make_surface(img_rgb)

    if polar_alignment_points:
        pygame.draw.lines(img_surface, WHITE, closed=False, points=polar_alignment_points, width=1)

    if relevant_stars:
        draw_stars(img_surface, relevant_stars, font)

    if polar_alignment_targets:
        (x1, y1), (x2, y2) = polar_alignment_targets
        # pin to the visible area
        x1 = min(max(1, x1), width - 1)
        y1 = min(max(1, y1), height - 1)
        x2 = min(max(1, x2), width - 1)
        y2 = min(max(1, y2), height - 1)
        size = 10
        thickness = 3
        # where we probed (debugging really)
        pygame.draw.rect(img_surface, WHITE, (x1 - 2, y1 - 2, 4, 4), 0)
        # where we are
        pygame.draw.circle(img_surface, WHITE, (width // 2, height // 2), size, 2)
        # where we need to go
        pygame.draw.line(img_surface, WHITE, (x2 - size, y2), (x2 + size, y2), thickness)
        pygame.draw.line(img_surface, WHITE, (x2, y2 - size), (x2, y2 + size), thickness)

    # we could include the x/y offset here and allow the out-of-frame DSOs to be
    # plotted outside the image, but that's a bit of a corner case. boom boom.
    if relevant_dsos:
        draw_dsos(img_surface, relevant_dsos, solver_hints.pixscale, font)

    def append_meta(s, key, prefix = "", suffix = "", align = True):
        return tab_append_lookup(meta, s, key, prefix, suffix, align)

    offset_v = (target_height - height) // 2
    offset_h = (target_width - width) // 2
    surface.fill(BLACK)
    surface.blit(img_surface, (offset_h, offset_v))

    # zoom should be minimal, it's really just for fine focus / framing
    if zoom:
        # TODO add a timeseries graph of recent focus magic scores
        # since live was enabled.
        if focus_magic:
            top_left = f"Focus magic: {focus_magic:.2f}"
            top_left_text = font.render(top_left, True, WHITE)
            top_left_rect = top_left_text.get_rect()
            top_left_rect.topleft = (10, 10)
            surface.blit(top_left_text, top_left_rect)
        return

    if (bitdepth := meta.get("BITDEPTH")) and not solved and stage is not Stage.LIVE:
        # the histogram and plate solving get in each others way we could add
        # the histogram back in for LIVE if we were to synthetically stretch the
        # pixels to the full exposure (or none needed) but that would mean
        # passing around some more meta data and it might get confusing.
        hist_width = 128
        hist, saturated = histogram(img_raw, hist_width, bitdepth)
        render_histogram(surface, hist, saturated, font, 10, 10)

    top_left = ""
    if stage is Stage.LIVE:
        top_left = append_meta(top_left, "SINGLE_EXPTIME", suffix = "s")
    else:
        top_left = append_meta(top_left, "EXPTIME", suffix = "s")
    top_left = append_meta(top_left, "GAIN", prefix=" ", suffix = "cB", align=False) # centibel = 0.1dB
    top_left = append_meta(top_left, "FILTER", prefix=" ", align=False)

    if solver_hints and solver_hints.focal_length:
        top_left = tab(top_left) + f"{solver_hints.focal_length}mm"

    top_left_text = font.render(top_left, True, WHITE)
    top_left_rect = top_left_text.get_rect()
    top_left_rect.topleft = (10, 10)
    surface.blit(top_left_text, top_left_rect)

    # this is an info area just below the top left
    top_left2 = ""
    if solve:
        match polar_align:
            case _ if not solved:
                top_left2 = "FAIL plate solve"
            case None:
                pass
            case ((ra, dec), None):
                # Having PEC would be nice but it's actually really hard to
                # estimate it without knowing the alt/az axis point. The best
                # we can do is just show the delta and the user can manually
                # choose a point that maximises it.
                delta_ra = (solver_hints.ra_center - ra) % 360
                if delta_ra > 180:
                    delta_ra = delta_ra - 360
                    delta_ra = abs(delta_ra)
                delta_dec = abs(solver_hints.dec_center - dec) / 2
                advice = ""
                if delta_ra < 10:
                    advice = " (too close)"
                if delta_ra > 95:
                    advice = " (too far)"
                top_left2 = f"Slew RA {format_dms(round(delta_ra))}{advice} → maximise drift {format_dms(delta_dec)} → press A"
            case ((ra1, dec1), (ra2, dec2)):
                top_left2 = f"polar align to crosshairs → press A"

    top_left2_text = font.render(top_left2, True, WHITE)
    top_left2_rect = top_left2_text.get_rect()
    top_left2_rect.topleft = (10, top_left_rect.bottom + 5) # just below the 1st line
    surface.blit(top_left2_text, top_left2_rect)

    # bottom left should indicate what the shutter will do in LIVE
    bottom_left = ""
    if stage == Stage.LIVE:
        bottom_left = tab(bottom_left) + "[LIVE"
        bottom_left = append_meta(bottom_left, "EXPTIME", suffix = "s")
        bottom_left += "]"

    bottom_left_text = font.render(bottom_left, True, WHITE)
    bottom_left_rect = bottom_left_text.get_rect()
    bottom_left_rect.bottomleft = (10, target_height - 10)
    surface.blit(bottom_left_text, bottom_left_rect)

    # icons here might be nicer
    top_right = ""

    if paused:
        top_right = tab(top_right) + "PAUSED"
    if saved:
        top_right = tab(top_right) + "SAVED"
    if out:
        top_right = tab(top_right) + Path(out).name.split(".")[0]

    top_right = tab(top_right) + mode.name
    if mode is Mode.INTERVALS:
        top_right = append_meta(top_right, "INTERVAL_INFO", prefix=" ", align=False)
    if mode != Mode.SINGLE:
        top_right = append_meta(top_right, "IMAGE_COUNT", prefix=" ", suffix="/∞")

    text = font.render(top_right, True, WHITE)
    rect = text.get_rect()
    rect.topright = (target_width - 10, 10)
    surface.blit(text, rect)

# transient preferences only valid for the session
# (not persisted to the settings, but maybe one day)
class Prefs:
    def __init__(self, mode = None, live_cap = None, plate_solve = None, hints = None):
        self.mode = mode
        self.live_cap = live_cap
        self.plate_solve = plate_solve

        # only preserve the last position; camera can change
        if hints:
            self.hints = SolverHints()
            if hints.ra_center is not None:
                self.hints.ra_center = hints.ra_center
            if hints.dec_center is not None:
                self.hints.dec_center = hints.dec_center
        else:
            self.hints = None

class Menu:
    def __init__(self, epaper, output_dir, camera, camera_settings, wheel, wheel_settings, prefs):
        self.screensaver = False
        mode = prefs.mode
        if not mode or (mode is Mode.INTERVALS and not camera_settings.intervals):
            mode = Mode.SINGLE

        surface = pygame.display.get_surface()
        w = surface.get_width()
        h = surface.get_height()

        self.epaper = epaper
        self.view = View(w, h)
        self.zoom = False # used to capture BACK

        self.menu = None
        if not camera:
            self.capture = None
            self.view.message("no camera")
            return

        self.capture = Capture(self.view, output_dir, camera, camera_settings, wheel, wheel_settings, mode)
        self.capture.start()

    def mk_secondary_action_menu(self):
        menu = luddcam_settings.mk_menu("Capture")
        # A design choice is to put all these things in the second
        # action menu. We might want to allow UP/DOWN (or X/Y)
        # shortcuts one day.
        def select_mode(a, mode):
            #print(f"setting mode to {mode}")
            self.capture.set_mode(mode)

        items = [("Single", Mode.SINGLE), ("Repeat", Mode.REPEAT)]
        if self.capture.camera_settings.intervals:
            items.append(("Intervals", Mode.INTERVALS))

        menu.add.selector(
            "Mode: ",
            items=items,
            default=self.capture.mode.value,
            onchange=select_mode,
            align=ALIGN_LEFT)

        def select_plate_solve(a):
            #print(f"toggling plate solving, got {a}")
            self.view.set_plate_solve(a)
        menu.add.toggle_switch(
            "Plate Solving",
            self.view.get_plate_solve(),
            state_text=('Off', 'Live'),
            onchange=select_plate_solve,
            state_color=(BLACK, BLACK),
            align=ALIGN_LEFT)

        def select_live_cap(a, val):
            with self.capture.lock:
                self.capture.live_cap = val

        live_exposure_options = [1, 2, 3, 4, 5]
        menu.add.selector(
            "Live limit: ",
            items = [(luddcam_settings.exposure_render(i), i) for i in live_exposure_options],
            default=live_exposure_options.index(self.capture.live_cap),
            onchange=select_live_cap,
            align=ALIGN_LEFT)

        return menu

    def get_prefs(self):
        mode = None
        live_cap = None
        if self.capture:
            with self.capture.lock:
                mode = self.capture.mode
                live_cap = self.capture.live_cap
        with self.view.lock:
            plate_solve = self.view.plate_solve
            hints = self.view.hints
        return Prefs(mode, live_cap, plate_solve, hints)

    def cancel(self):
        if self.capture:
            self.capture.set_stage(Stage.STOP)
            self.capture = None
        if self.screensaver:
            backlight_on()

    def update(self, events):
        surface = pygame.display.get_surface()

        if not self.capture:
            self.view.blit(surface)
            return

        if self.menu:
            for event in events:
                if is_back(event):
                    self.menu = None
                    return

            self.menu.update(events)
            self.menu.draw(surface)
            return True # don't delegate

        stage = self.capture.get_stage()
        #mode = self.capture.get_mode()

        acted = False
        for event in events:
            if self.screensaver and is_button(event):
                print("waking screen")
                self.screensaver = False
                backlight_on()
                self.epaper.wake()
                if not is_start(event):
                    continue

            if is_start(event):
                # print("SHUTTER")
                self.zoom = self.view.disable_zoom()
                if stage is Stage.CAPTURE:
                    self.capture.set_stage(Stage.PAUSE)
                else:
                    self.capture.set_stage(Stage.CAPTURE)
            elif is_back(event):
                self.zoom = self.view.disable_zoom()
                if stage == Stage.PAUSE:
                    self.capture.set_stage(Stage.LIVE)
                elif stage == Stage.LIVE:
                    self.menu = self.mk_secondary_action_menu()
                else:
                    # back is now screensaver
                    self.screensaver = True
                    print("blanking screen")
                    surface.fill(BLACK)
                    backlight_off()
                    self.epaper.sleep()
            elif is_action(event):
                if self.view.get_plate_solve() and stage is Stage.LIVE:
                    self.view.toggle_align()
                else:
                    # TODO when in planetary mode, zoom should go to the
                    # auto-selected planetary object. up/down should change the
                    # size of the FOV and left/right should go to the
                    # next/previous object. If two objects fit within a frame,
                    # we should add their CoM as a selectable option. When in
                    # that mode we should disable event delegation.
                    self.zoom = self.view.toggle_zoom()
            elif is_left(event) or is_right(event):
                acted = True
                if self.capture.camera_settings.intervals:
                    limit = len(Mode)
                else:
                    limit = len(Mode) - 1
                if is_left(event):
                    direction = -1
                else:
                    direction = 1
                new_mode_idx = (self.capture.get_mode().value + direction) % limit
                self.capture.set_mode(Mode(new_mode_idx))
            elif is_up(event):
                acted = True
                self.view.set_plate_solve(not self.view.get_plate_solve())

        if not self.screensaver:
            self.view.blit(surface)

        return acted

if __name__ == "__main__":
    pygame.font.init()

    exp = 10.0
    # 3 minute dual band exposure
    # f = "test_data/osc/exposures/10.fit.fz"
    # actually a 30 second rgb exposure
    f = "test_data/osc/exposures/1.fit.fz"
    # sony a7iii is RGGB

    img_raw, h = load_fits(f)

    bayer = get_corrected_bayer(h)
    # print(f"BAYER={bayer}")

    # bayer = "RGGB"
    #bayer = None

    # a picture of a tree
    # f = "tmp/IMG_00007.fit.fz"
    # G3M715C is GRBG
    # bayer = "GRBG"
    # ASI585MC is RGGB
    # bayer = "RGGB"
    #exp = 0.001

    surface = pygame.Surface((800, 600))
    # flipping a fits image has the impact of getting us back to sensor
    # coordinates (zero is top left).

    zoom = False
    meta = {
        "BITDEPTH": 14,
        "IMAGE_COUNT": 1,
        "EXPTIME": exp,
        "GAIN": 180.0,
        "FILTER": "L",
        "BAYERPAT": bayer,
        "STAGE": Stage.LIVE,
        "MODE": Mode.SINGLE,
        "XPIXSZ": 5.94
    }
    out = "/foo/bar/IMG_00001.fits"
    font = pygame.font.Font(luddcam_settings.hack, 14)

    hints = SolverHints()
    # hints.ra_center = 10.70998331
    # hints.dec_center = 41.256808731
    # hints.pixscale = 16.477246715
    # hints.focal_length = 595

    #align = None
    align = ((0, 41.5), None)
    #align = ((0, 41.5), (10.7, 50))

    start = time.perf_counter()
    render_frame_for_screen(surface, img_raw, zoom, meta, out, font, False, False, hints, align)
    end = time.perf_counter()
    print(f"rendering took {end - start:.2f}")

    pygame.image.save(surface, "test.png")

    subprocess.Popen(["feh", "--force-aliasing", "test.png"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Local Variables:
# compile-command: "python3 luddcam_capture.py"
# End:
