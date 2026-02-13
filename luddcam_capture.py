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


from datetime import datetime, timezone
from enum import Enum
from fractions import Fraction
from pathlib import Path
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import traceback
import time

import PIL.Image as Image
import fitsio
import numpy as np
import pygame

import pygame_menu

import luddcam_astrometry
import luddcam_catalog
import luddcam_settings
from luddcam_settings import is_back, is_left, is_right, is_up, is_down, is_start, is_action, is_button
import mocks

ALIGN_LEFT=pygame_menu.locals.ALIGN_LEFT

WHITE=(255, 255, 255)
BLACK=(0, 0, 0)

class Mode(Enum):
    SINGLE = 0
    REPEAT = 1
    INTERVALS = 2

class Stage(Enum):
    LIVE = 0 # actively capturing data in a fixed loop and reduced exposure
    CAPTURE = 1 # actively capturing data with the user's settings
    PAUSE = 2 # not capturing data, showing last capture on screen
    STOP = 3 # shutting down

# TODO compression is actually quite slow, so maybe make this a setting at some
# point if it can be justified. Compression is usually a little less than 50% so
# it's hard to justify the cost on a battery powered rpi. Maybe worth it for
# long term storage. Even taking images every 10 seconds for 8 hours, an imx585
# would consume 50gb of space, so it seems better to just stick with
# uncompressed generally.
compression_enabled = False

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

                if stage is Stage.LIVE and capture_exposure > 1:
                    # intentionally limit live exposures
                    capture_exposure = 1

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
                interval_info = f"sub {sub} in step {index + 1} of {steps}"
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

def mk_metadata(exp, camera, filt, cooling, plate):
    # note that fitsio seems to automatically set BZERO and BSCALE
    metadata = []
    metadata.append(("PROGRAM", "luddcam"))
    # DATE is junk on raspberry pis without batteries
    metadata.append(("DATE", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")))
    metadata.append(("EXPTIME", exp))
    if filt:
        metadata.append(("FILTER", filt))
    metadata.append(("BITDEPTH", camera.bitdepth))

    metadata.append(("XPIXSZ", camera.pixelsize))
    metadata.append(("YPIXSZ", camera.pixelsize))
    metadata.append(("INSTRUME", camera.name))
    if (temp := camera.get_temp()) is not None:
        metadata.append(("CCD-TEMP", temp))
    if camera.is_cooled and cooling is not None:
        metadata.append(("SET-TEMP", cooling))
    if camera.gain is not None:
        metadata.append(("GAIN", camera.gain))
    if camera.offset is not None:
        metadata.append(("OFFSET", camera.offset))
    if camera.bayer:
        # https://siril.readthedocs.io/en/stable/file-formats/FITS.html#orientation-of-fits-images
        metadata.append(("BAYERPAT", camera.bayer))
        metadata.append(("ROWORDER", "BOTTOM-UP"))

    # these are pulled from the last known good solve
    # so may be inaccurate and delayed, but when it
    # works its a decent hint to siril.
    # https://siril.readthedocs.io/en/stable/file-formats/FITS.html#list-of-fits-keywords
    if ra := plate.ra_center:
        metadata.append(("RA", ra))
    if dec := plate.dec_center:
        metadata.append(("DEC", dec))
    if focal_length := plate.focal_length:
        metadata.append(("FOCALLEN", focal_length))

    return metadata

def save_fits(out, view, data, metadata, background = False):
    # print(f"...saving to {out}")
    writer = FitsWriter(view, data, out, metadata)
    if background:
        writer.start()
    else:
        writer.run()

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

    # call this to do the run on a background thread
    def start(self):
        thread = threading.Thread(target=self.run, daemon=False, name=f"FitsWriter {self.out}")
        thread.start()

    def run(self):
        try:
            start = time.perf_counter()
            with fitsio.FITS(self.out, "rw") as fits:
                # note that rice encoding can be lossy for floating point
                #
                # compression is disabled here because it all happens inside the
                # GIL which impacts the app responsivity (and the tests).
                # https://github.com/esheldon/fitsio/issues/474
                #
                # we flip on write because fits starts at the bottom
                # but the camera SDKs (and common image formats) start
                # at the top.
                fits.write(np.flipud(self.data), compress=None)
                hdu = fits[-1]
                for k, v in self.metadata:
                    hdu.write_key(k, v)

            # we're using removable media, so we want to flush our writes.
            #
            # Unfortunately, fsync holds the GIL so we don't flush when using
            # compression. We could use the linux tool sync but it flushes
            # the entire disk, which is excessive.
            if compression_enabled and have_fpack:
                subprocess.run(["fpack", "-D", "-g1", self.out], check=True)
                with open(self.out + ".fz", "rb+") as f:
                    os.fsync(f.fileno())
                # if sys.platform.startswith("linux"):
                #     subprocess.run(["sync", "-f", self.out + ".fz"])
            else:
                # if sys.platform.startswith("linux"):
                #     subprocess.run(["sync", "-f", self.out])
                # sub-optimal, as it holds the GIL, but less invasive
                with open(self.out, "rb+") as f:
                    os.fsync(f.fileno())

            end = time.perf_counter()
            print(f"FITS.write: {end - start:.4f} {self.out}")
            if self.view:
                self.view.save(self.out)
        except Exception as e:
            # TODO should we try again?
            print(f"Failed to write {self.out}")
            traceback.print_exc()

have_fpack = shutil.which("fpack") is not None

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
        self.scale = None # relative to the raw image
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

    start = time.perf_counter()
    bayer = meta.get("BAYERPAT")
    img_rgb, img_mono = downscale(img_raw, target_width, target_height, zoom, bayer, solve)
    height, width, _ = img_rgb.shape
    end = time.perf_counter()
    print(f"downscale took {end - start}")

    # FIXME implement focus helper and consider the trade off between doing the
    # SEP on a quality downscaled image vs fast downscale with full res SEP
    # (after bayering... i.e. img_rgb and img_mono would be different sizes but
    # that can be fixed by a scaling factor).

    # plate solving is relatively expensive and it might seem silly to do it
    # here and block this thread, but it is the only way to do it in a way
    # that looks good to the user. We could consider doing a basic centroid
    # conversion and reuse the last solution to save some cycles, or look
    # into having a hot server wrapper over solve-field if it's a problem.
    solved, relevant_stars, relevant_dsos, polar_alignment_points, polar_alignment_targets = plate_solve(solver_hints, img_mono, raw_height, meta.get("XPIXSZ"), polar_align)

    img_rgb = quantize(img_rgb, meta["EXPTIME"] >= 0.1)

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

    def tab(s):
        if len(s) == 0:
            return s
        if not s.endswith(" "):
            s += " "
        tab = 4
        need = (-len(s)) % tab
        #print(f"adding {need} spaces to '{s}'")
        return s + " " * need

    def append_meta(s, key, prefix = "", suffix = "", align = True):
        if (v := meta.get(key)):
            if align:
                s = tab(s)
            if isinstance(v, float):
                if v.is_integer():
                    v = str(int(v))
                else:
                    frac = Fraction(v).limit_denominator(100000)
                    v = f"{frac.numerator}/{frac.denominator}"
            return s + prefix + str(v) + suffix
        return s

    offset_v = (target_height - height) // 2
    offset_h = (target_width - width) // 2
    surface.fill((0,0,0,0))
    surface.blit(img_surface, (offset_h, offset_v))

    # zoom should be minimal, it's really just for fine focus / framing
    if zoom:
        return

    if (bitdepth := meta.get("BITDEPTH")) and not (solve and stage == Stage.LIVE):
        # don't render the histogram when doing live plate solving as they
        # interfere with each other.
        hist_width = 128
        hist, saturated = histogram(img_raw, hist_width, bitdepth)
        render_histogram(surface, hist, saturated, font, 10, 10)

    top_left = ""
    if out:
        top_left = tab(top_left) + pathlib.Path(out).name.split(".")[0]

    top_left = append_meta(top_left, "EXPTIME", suffix = "s")
    top_left = append_meta(top_left, "GAIN", prefix=" ", suffix = "cB", align=False) # centibel = 0.1dB
    top_left = append_meta(top_left, "FILTER", prefix=" ", align=False)

    if stage == Stage.LIVE:
        top_left = tab(top_left) + stage.name
    else:
        top_left = tab(top_left) + mode.name
        if mode != Mode.SINGLE:
            # TODO when in interval it should be c/step_total of step/steps_total
            # instead of infinity.
            top_left = append_meta(top_left, "IMAGE_COUNT", prefix=" ", suffix="/∞", align=False)

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
                delta_ra = (solver_hints.ra_center - ra) % 360
                if delta_ra > 180:
                    delta_ra = delta_ra - 360
                    delta_ra = abs(delta_ra)
                delta_dec = abs(solver_hints.dec_center - dec) / 2
                pec = ""
                if delta_ra > 1:
                    pec = round(60 * 60 * delta_dec / delta_ra)
                    pec = f" ({pec}\")"
                if not any(0 <= x < width and 0 <= y < height for x, y in polar_alignment_points):
                    # we've gone so far we can't see the DEC anymore
                    top_left2 = f"slew RA closer{pec}"
                elif delta_ra < 10:
                    top_left2 = f"slew RA further{pec}"
                else:
                    top_left2 = f"press A to polar align{pec}"
            case ((ra1, dec1), (ra2, dec2)):
                top_left2 = f"center target with alt/az, press A to exit"

    top_left2_text = font.render(top_left2, True, WHITE)
    top_left2_rect = top_left2_text.get_rect()
    top_left2_rect.topleft = (10, top_left_rect.bottom + 5) # just below the 1st line
    surface.blit(top_left2_text, top_left2_rect)

    # bottom left should indicate what the shutter will do in LIVE
    bottom_left = ""
    if stage == Stage.LIVE:
        bottom_left += "[" + mode.name
        if mode is Mode.INTERVALS:
            bottom_left = append_meta(bottom_left, "INTERVAL_INFO")
        else:
            bottom_left = append_meta(bottom_left, "SINGLE_EXPTIME", suffix = "s")
        bottom_left += "]"

    bottom_left_text = font.render(bottom_left, True, WHITE)
    bottom_left_rect = bottom_left_text.get_rect()
    bottom_left_rect.bottomleft = (10, target_height - 10)
    surface.blit(bottom_left_text, bottom_left_rect)

    # icons here might be nicer
    top_right = ""
    if saved:
        top_right = tab(top_right) + "SAVED"
    if paused:
        top_right = tab(top_right) + "PAUSED"

    if top_right:
        text = font.render(top_right, True, WHITE)
        rect = text.get_rect()
        rect.topright = (target_width - 10, 10)
        surface.blit(text, rect)

def plate_solve(hints, img_mono, raw_height, pixel_size, polar_align):
    if hints is None or img_mono is None:
        return False, None, None, None, None

    height, width = img_mono.shape
    scale_factor = raw_height / height
    centroids = luddcam_astrometry.source_extract(img_mono)
    scale_hint = hints.pixscale
    if scale_hint is None:
        scale_hint = (scale_factor * 0.5, None)
    pos_hint = (hints.ra_center, hints.dec_center)
    parity_hint = hints.parity

    relevant_stars, relevant_dsos, polar_alignment_points, polar_alignment_targets = None, None, None, None
    if len(centroids) > 10:
        with luddcam_astrometry.Astrometry() as solver:
            bounds = solver.solve_field(centroids, width, height, pos_hint, scale_hint, parity_hint)
            if not bounds and parity_hint:
                # if we had some hints and it still failed, try with reduced
                # hints. The only way to reset after this is to go into the
                # menu and come back, e.g. if the user changed the
                # backspacing or optics.
                bounds = solver.solve_field(centroids, width, height, None, scale_hint, parity_hint)
            # to get a starting point
            if not bounds:
                return False, None, None, None, None
            #print(bounds)
            ra_min = bounds["ramin"]
            ra_max = bounds["ramax"]
            hints.ra_center = bounds["ra_center"]
            dec_min = bounds["decmin"]
            dec_max = bounds["decmax"]
            hints.dec_center = bounds["dec_center"]
            hints.pixscale = bounds["pixscale"]
            hints.parity = bounds["parity"]
            hints.scale = scale_factor

            if pixel_size:
                hints.focal_length = round((scale_factor * pixel_size / hints.pixscale) * 206.265)
                # print(f"focal_length = {focal_length}")

            print(f"plate solved at {hints.ra_center},{hints.dec_center} scale {hints.pixscale} with {hints.focal_length}mm")

            match polar_align:
                case None:
                    stars = luddcam_catalog.relevant_stars(dec_min, dec_max, ra_min, ra_max)
                    dsos = luddcam_catalog.relevant_dsos(dec_min, dec_max, ra_min, ra_max)
                    relevant_stars = solver.with_radec_to_pixels(stars)
                    relevant_dsos = solver.with_radec_to_pixels(dsos)
                case ((ra1, dec1), None):
                    ras = [(ra, dec1) for ra in np.linspace(ra_min, ra_max, 100)]
                    polar_alignment_points = [tuple(a) for a in solver.radec_to_pixels(ras)]
                case ((ra1, dec1), (ra2, dec2)):
                    targets = [
                        # where we probed
                        (ra2, dec2),
                        # where to go
                        (ra2, (dec1 + dec2) / 2)
                    ]
                    polar_alignment_targets = [tuple(a) for a in solver.radec_to_pixels(targets)]

    return True, relevant_stars, relevant_dsos, polar_alignment_points, polar_alignment_targets

def draw_stars(surface, stars, font):
    for star in stars:
        # adding 0.5 to improve rounding
        x = star["x"] + 0.5
        y = star["y"] + 0.5
        text = font.render(star["name"], True, WHITE)
        surface.blit(text, (x + 10, y - text.get_height() // 2))

arrows = {
    (-1, -1): "↖", (0, -1): "↑", (1, -1): "↗",
    (-1, 0): "←",                (1, 0): "→",
    (-1, 1): "↙",  (0, 1): "↓",  (1, 1): "↘"
}
def draw_dsos(surface, dsos, pixscale, font):
    width, height = surface.get_size()
    def draw_labelled_dso(dso, mark = None):
        # adding 0.5 to improve rounding
        x = dso["x"] + 0.5
        y = dso["y"] + 0.5

        # clamped text
        margin = 10
        cx = max(margin, min(width - margin, x))
        cy = max(margin, min(height - margin, y))

        label = dso["name"]
        if mark:
            if (x < width // 2):
                label = f"{mark}{label}"
            else:
                label = f"{label}{mark}"

        text = font.render(label, True, WHITE)
        tx = cx - text.get_width() // 2
        ty = cy - text.get_height() // 2
        # clamp to screen
        tx = max(4, min(tx, width - text.get_width() - 4))
        ty = max(4, min(ty, height - text.get_height() - 4))
        surface.blit(text, (tx, ty))

        # the position circle is only useful for very big things
        if pixscale:
            radius_px = max(4, (dso.get("diameter", 0) * 60) / (2 * pixscale))
            if radius_px > text.get_width() // 2:
                pygame.draw.circle(surface, WHITE, (x, y), radius_px, width=1)

    def direction_indicator(x, y, width, height):
        dx = 1 if x >= width else (-1 if x < 0 else 0)
        dy = 1 if y >= height else (-1 if y < 0 else 0)
        return arrows.get((dx, dy))

    in_frame = []
    out_frame = {}
    margin = 50

    for dso in dsos:
        x = dso["x"]
        y = dso["y"]
        if margin <= x < (width - margin) and margin <= y < (height + margin):
            in_frame.append(dso)
        else:
            ind = direction_indicator(x, y, width, height)
            dist = ((x - width/2)**2 + (y - height/2)**2)**0.5
            last = out_frame.get(ind)
            if not last or dist < last[1]:
                out_frame[ind] = (dso, dist)

    # these can all still overlap
    for dso in in_frame:
        draw_labelled_dso(dso)

    for mark, (dso, _) in out_frame.items():
        draw_labelled_dso(dso, mark)

class Menu:
    # TODO plate solving state should probably be remembered too
    def __init__(self, epaper, output_dir, camera, camera_settings, wheel, wheel_settings, mode):
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

        self.screensaver = False

    def mk_secondary_action_menu(self):
        menu = luddcam_settings.mk_menu("Capture")
        # A design choice is to put all these things in the second
        # action menu. We might want to allow UP/DOWN (or X/Y)
        # shortcuts one day.
        def select_mode(a, mode):
            print(f"setting mode to {mode}")
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

        return menu

    def get_mode(self):
        if self.capture:
            return self.capture.mode

    def cancel(self):
        if self.capture:
            self.capture.set_stage(Stage.STOP)
            self.capture = None
        if self.screensaver:
            backlight_on()

    def update(self, events):
        screen = pygame.display.get_surface()

        if not self.capture:
            self.view.blit(screen)
            return

        if self.menu:
            for event in events:
                if is_back(event):
                    self.menu = None
                    return

            self.menu.update(events)
            self.menu.draw(screen)
            return

        stage = self.capture.get_stage()

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
                    screen.fill(BLACK)
                    backlight_off()
                    self.epaper.sleep()
            elif is_action(event):
                if self.view.get_plate_solve():
                    self.view.toggle_align()
                else:
                    # TODO zoom should be changed to a two stage process: first
                    # click shows a box, arrows move around, second click goes in
                    # (arrows still work). The settings should persist per camera.
                    # we need a way to either reset or (preferred) visually
                    # highlight that we're dead on center, maybe by showing a faded
                    # version of the center view.
                    self.zoom = self.view.toggle_zoom()

        if not self.screensaver:
            self.view.blit(screen)

# Downscales the (bayered) image to fit within the target width/height returning
# an rgb image (retaining the original bittype) and an (optional) float32 mono
# variant.
#
# we might want to consider returning the mono image prior to binning, to improve
# the chances of a good plate solve. But this will mean handling the ratios
# in the caller.
#
# this can be surprisingly computationally expensive if we are not careful,
# so we aim for speed above quality. General rescaling with nearest neighbour
# turned out to be pretty bad quality (and certainly not worth the CPU).
#
# zoom means to crop to the target size (uses higher quality debayer)
def downscale(mono, target_width, target_height, zoom, bayer, gray):
    height, width = mono.shape
    if target_height > height or target_width > width:
        raise ValueError(f"downscale doesn't upscale ({mono.shape} => ({height},{width}))")

    if zoom:
        startx = even_down(width // 2 - target_width // 2)
        starty = even_down(height // 2 - target_height // 2)
        mono = mono[starty:starty + target_height, startx:startx + target_width]
        if bayer:
            # doesn't downsample
            rgb = debayer_quality(mono, bayer)
    else:
        if bayer:
            # downsamples
            rgb = debayer_fast(mono, bayer)
            rgb = pixel_bin(rgb, target_width, target_height)
        else:
            mono = pixel_bin(mono, target_width, target_height)
            rgb = np.stack([mono] * 3, axis=-1)

    grayscale = None
    if bayer:
        if gray:
            grayscale = rgb @ np.array([0.299, 0.587, 0.114])
        return (rgb, grayscale)
    else:
        if gray:
            grayscale = rgb[:, :, 0].astype(np.float32)
        return (rgb, grayscale)

# bins (averages) pixels until the img fits within the target size
def pixel_bin(img, target_width, target_height):
    height, width = img.shape[:2]
    if width > target_width or height > target_height:
        new_h = (height // 2) * 2
        new_w = (width // 2) * 2
        trimmed = img[:new_h, :new_w]
        img = (trimmed[0::2, 0::2] + trimmed[0::2, 1::2] +
               trimmed[1::2, 0::2] + trimmed[1::2, 1::2]) // 4
        return pixel_bin(img, target_width, target_height)
    return img

# quantizes an image to 8 bits with astro specific noise/saturation
# or an optional asinh stretch.
def quantize(img, stretch):
    # now quantise to 8 bits
    if img.dtype == np.uint8:
        if stretch:
            return asinh_lut_8[img]
        return img
    else:
        # start = time.perf_counter()
        sampled = img[::2, ::2] # speeds things up
        sampled = sampled[sampled > 0] # ignores letterboxing
        # faster ways to calculate bounds, more generic
        #lo = np.min(sampled)
        #hi = np.max(sampled)
        # astro specific, and a bit slower, but prettier
        lo = np.median(sampled) # astro specific noise level
        hi = np.percentile(sampled, 99.9) # guaranteed saturation
        # end = time.perf_counter()
        # print(f"finding the quantization parameters took {end - start:.2f} ")
        # print(f"lo={lo},hi={hi}")
        if lo < hi:
            if stretch:
                # keep everything in 16 bit as long as possible
                m = 65535.0 / (hi - lo)
                img = ((img.astype(np.int32) - lo) * m).clip(0, 65535).astype(int)
                return asinh_lut_16[img]
            else:
                m = 255.0 / (hi - lo)
                return ((img.astype(np.int32) - lo) * m).clip(0, 255).astype(np.uint8)
        else:
            if stretch:
                return asinh_lut_16[img]
            return (img >> 8).astype(np.uint8)

# the nearest even, rounding up
def even_up(i):
    return (i + 1) & ~1

# the nearest event, rounding down
def even_down(i):
    return i & ~1

# nearest neighbour numpy image resize, pretty shitty
def resize_nn(img, tw, th):
    h, w = img.shape[:2]
    scale_x = w / tw
    scale_y = h / th
    # map dest pixel centers back to src pixel centers
    xs = ((np.arange(tw) + 0.5) * scale_x - 0.5).astype(int)
    ys = ((np.arange(th) + 0.5) * scale_y - 0.5).astype(int)
    xs = np.clip(xs, 0, w - 1)
    ys = np.clip(ys, 0, h - 1)
    return img[np.ix_(ys, xs)]  # handles arbitrary dimensions

# debayer the mono image using the given pattern, using fast downsampling
# that reduces the resolution of the returned image and only using one green
# pixel in every block.
def debayer_fast(data, bayer):
    # print(f"debayering an {data.shape} image with {bayer} pattern")
    h, w = data.shape
    if h % 2 or w % 2:
        raise ValueError(f"debayer_simple expects even dimensions (got {h},{w})")

    dt = data.dtype

    tl = data[0::2, 0::2]
    tr = data[0::2, 1::2]
    #bl = data[1::2, 0::2]
    #br = data[1::2, 1::2]

    # pick one of the greens and throw the other away for perf,
    # saves constructing an array and a merge.
    if bayer == "RGGB":
        br = data[1::2, 1::2]
        R, G, B = tl, tr, br
    elif bayer == "BGGR":
        br = data[1::2, 1::2]
        R, G, B = br, tr, tl
    elif bayer == "GRBG":
        bl = data[1::2, 0::2]
        R, G, B = tr, tl, bl
    elif bayer == "GBRG":
        bl = data[1::2, 0::2]
        R, G, B = bl, tl, tr
    else:
        raise ValueError(f"Unsupported Bayer pattern: {bayer}")

    return np.stack((R, G, B), axis=-1)

# like fast but uses a little more memory and uses all pixels
def debayer_fastish(data, bayer):
    # print(f"debayering an {data.shape} image with {bayer} pattern")
    h, w = data.shape
    if h % 2 or w % 2:
        raise ValueError(f"debayer_simple expects even dimensions (got {h},{w})")

    dt = data.dtype

    tl = data[0::2, 0::2]
    tr = data[0::2, 1::2]
    bl = data[1::2, 0::2]
    br = data[1::2, 1::2]

    def avg(a, b):
        return np.mean([a, b], axis=0).round().astype(data.dtype)

    if bayer == "RGGB":
        R, G, B = tl, avg(tr, bl), br
    elif bayer == "BGGR":
        R, G, B = br, avg(tr, bl), tl
    elif bayer == "GRBG":
        R, G, B = tr, avg(tl, br), bl
    elif bayer == "GBRG":
        R, G, B = bl, avg(tl, br), tr
    else:
        raise ValueError(f"Unsupported Bayer pattern: {bayer}")

    return np.stack([R, G, B], axis=-1)

# slower debayer (higher memory usage) that retains the original resolution by
# interpolating pixels. This is best for OSC guide cameras (after grayscaling)
# so that we get the best possible centroids.
def debayer_quality(data, bayer):
    # print(f"debayering an {data.shape} image with {bayer} pattern")
    h, w = data.shape
    if h % 2 or w % 2:
        raise ValueError(f"debayer_simple expects even dimensions (got {h},{w})")

    r_mask = np.zeros(data.shape, dtype=bool)
    g_mask = np.zeros(data.shape, dtype=bool)
    b_mask = np.zeros(data.shape, dtype=bool)

    # tl = data[0::2, 0::2]
    # tr = data[0::2, 1::2]
    # bl = data[1::2, 0::2]
    # br = data[1::2, 1::2]
    if bayer == "RGGB":
        r_mask[0::2, 0::2] = True
        g_mask[0::2, 1::2] = True
        g_mask[1::2, 0::2] = True
        b_mask[1::2, 1::2] = True
    elif bayer == "BGGR":
        b_mask[0::2, 0::2] = True
        g_mask[0::2, 1::2] = True
        g_mask[1::2, 0::2] = True
        r_mask[1::2, 1::2] = True
    elif bayer == "GRBG":
        g_mask[0::2, 0::2] = True
        r_mask[0::2, 1::2] = True
        b_mask[1::2, 0::2] = True
        g_mask[1::2, 1::2] = True
    elif bayer == "GBRG":
        g_mask[0::2, 0::2] = True
        b_mask[0::2, 1::2] = True
        r_mask[1::2, 0::2] = True
        g_mask[1::2, 1::2] = True
    else:
        raise ValueError(f"Unsupported Bayer pattern: {bayer}")

    # we allocate a temporary channel that is padded (to handle the edges) with
    # NaNs where there is no data for that channel. Then we convolve taking the
    # center pixel if there is data, otherwise the average of all non-NaN
    # values. Unfortunately this is eager, no way to do this lazily in numpy.
    def interpolate(mask):
        # so many intermediate arrays, le sigh
        c = np.full((h + 2, w + 2), np.nan, dtype=np.float32)
        c[1:-1, 1:-1][mask] = data[mask]
        centre = c[1:-1, 1:-1]
        averaged = np.nanmean([
            c[ :-2, :-2], c[ :-2, 1:-1], c[ :-2, 2:],
            c[1:-1, :-2], c[1:-1, 1:-1], c[1:-1, 2:],
            c[2:  , :-2], c[2:  , 1:-1], c[2:  , 2:]
        ], axis=0)
        return np.where(np.isnan(centre), averaged, centre).round().astype(data.dtype)

    r = interpolate(r_mask)
    g = interpolate(g_mask)
    b = interpolate(b_mask)
    # print(f"input was {data.shape}, r={r.shape}, g={g.shape}, b={b.shape}")

    return np.stack([r, g, b], axis=-1)

def lut_asinh_8(k):
    x = np.linspace(0, 1, 256)
    y = np.arcsinh(k * x) / np.arcsinh(k)
    return (y * 255 + 0.5).astype(np.uint8)
asinh_lut_8 = lut_asinh_8(15)

# quantizes 16 bit input to stretched 8 bit
def lut_asinh_16(k):
    x = np.linspace(0, 1, 65536)
    y = np.arcsinh(k * x) / np.arcsinh(k)
    return (y * 255 + 0.5).astype(np.uint8)
asinh_lut_16 = lut_asinh_16(15)

# returns a tuple of normalised histogram weights and the absolute count of
# saturated pixels. Log scale.
def histogram(img_raw, bins, bitdepth):
    max_val = 1 << bitdepth
    data = img_raw.ravel()
    data = data.clip(0, max_val - 1) # safety
    # np.bincount is much faster than np.histogram
    full = np.bincount(data, minlength=max_val)
    factor = max_val // bins
    hist = full.reshape(bins, factor).sum(axis=1)
    saturated = hist[-1]
    hist = np.log1p(hist)
    if hist.max() > 0:
        hist = hist / hist.max()
    return hist, saturated

def render_histogram(surface, hist, saturated, font,
                     padding_right = 0, padding_bottom = 0,
                     colour=WHITE):
    width = len(hist)
    # print(width)
    height = width // 2
    s_w, s_h = surface.get_size()
    base_w = s_w - padding_right - width
    base_h = s_h - padding_bottom
    # print(f"{s_w},{s_h},{base_w},{base_h}")

    for x, v in enumerate(hist):
        y = int(v * height)
        pygame.draw.line(surface, colour, (base_w + x, base_h), (base_w + x, base_h - y))

    # if you have hot pixels, you'll always hear about them
    if saturated > 0:
        if saturated > 999:
            txt = "!!!"
        else:
            txt = f"{saturated}*"
        surf = font.render(txt, True, colour)
        rect = surf.get_rect()
        w = s_w - padding_right
        h = s_h - padding_bottom - height + 5
        rect.topright = (w, h)
        surface.blit(surf, rect)

BACKLIGHT_DIR = "/sys/class/backlight"
def backlight_off(base=BACKLIGHT_DIR):
    if not os.path.isdir(base):
        return
    for dev in os.listdir(base):
        path = os.path.join(base, dev, "brightness")
        if os.path.exists(path):
            with open(path, "w") as f:
                f.write(str(0))

def backlight_on(base=BACKLIGHT_DIR):
    for dev in os.listdir(base):
        path = os.path.join(base, dev, "brightness")
        path_m = os.path.join(base, dev, "max_brightness")
        max_brightness = 255
        if os.path.exists(path_m):
            max_brightness = int(open(path_m).read())
        if os.path.exists(path):
            with open(path, "w") as f:
                f.write(str(max_brightness))

if __name__ == "__main__":
    pygame.font.init()

    exp = 10.0
    # 3 minute dual band exposure
    # f = "test_data/osc/exposures/10.fit.fz"
    # actually a 30 second rgb exposure
    f = "test_data/osc/exposures/1.fit.fz"
    # sony a7iii is RGGB

    h = fitsio.FITS(f)[1].read_header()
    bayer = h.get("BAYERPAT")
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
    img_raw = np.flipud(fitsio.FITS(f)[1].read())

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
    hints.ra_center = 10.70998331
    hints.dec_center = 41.256808731
    hints.pixscale = 16.477246715
    hints.focal_length = 595

    align = ((0, 41.5), None)
    #align = ((0, 41.5), (10.7, 41.3))

    start = time.perf_counter()
    render_frame_for_screen(surface, img_raw, zoom, meta, out, font, False, False, hints, align)
    end = time.perf_counter()
    print(f"rendering took {end - start:.2f}")

    pygame.image.save(surface, "test.png")
    # Image.fromarray(out).save("test.png")

    subprocess.Popen(["feh", "--force-aliasing", "test.png"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Local Variables:
# compile-command: "python3 luddcam_capture.py"
# End:
