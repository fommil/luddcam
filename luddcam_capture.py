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
# TODO X/Y and L/R on a SNES controller could be used for gain / exposure.
# TODO DOWN is free, we could use it for plate solving

from datetime import datetime, timezone
from enum import Enum
from fractions import Fraction
from pathlib import Path
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

import luddcam_settings
from luddcam_settings import is_back, is_left, is_right, is_up, is_down, is_start, is_action, is_button
import mocks

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
            metadata = mk_metadata(capture_exposure, self.camera, filt, self.camera_settings.cooling)
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

def mk_metadata(exp, camera, filt = None, cooling = None):
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

        self.font_large = pygame.font.Font(luddcam_settings.hack, 32)
        self.font_small = pygame.font.Font(luddcam_settings.hack, 14)

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
            # doing it here instead of after avoids many timing issues
            self.stale = False
        if not stale:
            return

        if isinstance(img_raw, str) or img_raw is None:
            surface.fill((0, 0, 0))
            if img_raw:
                text = self.font_large.render(img_raw, True, (255, 255, 255))
                rect = text.get_rect(center=(surface.get_width()//2, surface.get_height()//2))
                surface.blit(text, rect)
        else:
            is_saved = saved == out
            render_frame_for_screen(surface, img_raw, zoom, meta, out, self.font_small, is_paused, is_saved)

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

# moved out for easier manual testing
def render_frame_for_screen(surface, img_raw, zoom, meta, out, font, paused, saved):
    target_width, target_height = surface.get_size()
    #print(surface.get_size())
    start = time.perf_counter()
    bayer = meta.get("BAYERPAT")
    img_rgb = downscale(img_raw, target_width, target_height, zoom, bayer)
    end = time.perf_counter()
    #print(f"scaling for the screen took {end - start:.2f}")
    #print(img_rgb.shape)

    # we calculate the full histogram later, but this quick approximation
    # is basically free and handles colour / zoom / fov.

    if meta["EXPTIME"] >= 0.1:
        # simple stretch for astro exposures
        # (allows terrestrial use, e.g. daytime setup)
        # print("stretching")
        img_rgb = asinh_lut[img_rgb]
    # pygame expects (w,h,3) but everything else is (h,w,3)
    img_rgb = np.transpose(img_rgb, (1, 0, 2))
    # and it's also upside down compared to the rest of the world
    #img_rgb = img_rgb[::-1, ::-1, :]

    pygame.surfarray.blit_array(surface, img_rgb)

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

    # potentially skip the histogram in live view too
    if not zoom:
        w, h = surface.get_size()

        if (bitdepth := meta.get("BITDEPTH")):
            width = 128
            hist, saturated = histogram(img_raw, width, bitdepth)
            render_histogram(surface, hist, saturated, font, 10, 10)

        mode = meta["MODE"]
        stage = meta["STAGE"]
        top_left = ""
        bottom_left = ""
        if out:
            top_left = tab(top_left) + pathlib.Path(out).name.split(".")[0]

        top_left = append_meta(top_left, "EXPTIME", suffix = "s")
        top_left = append_meta(top_left, "GAIN", prefix=" ", suffix = "cB", align=False) # centibel = 0.1dB
        top_left = append_meta(top_left, "FILTER", prefix=" ", align=False)

        if stage == Stage.LIVE:
            # TODO when it is live we should include info about the stage
            # in the bottom left.
            top_left = tab(top_left) + stage.name
        else:
            top_left = tab(top_left) + mode.name
            if mode != Mode.SINGLE:
                # TODO when in interval it should be c/step_total of step/steps_total
                # instead of infinity.
                top_left = append_meta(top_left, "IMAGE_COUNT", prefix=" ", suffix="/∞", align=False)

        top_left_text = font.render(top_left, True, (255, 255, 255))
        top_left_rect = top_left_text.get_rect()
        top_left_rect.topleft = (10, 10)
        surface.blit(top_left_text, top_left_rect)

        # bottom left should indicate what the shutter will do in LIVE
        bottom_left = ""
        if stage == Stage.LIVE:
            bottom_left += "[" + mode.name
            if mode is Mode.INTERVALS:
                bottom_left = append_meta(bottom_left, "INTERVAL_INFO")
            else:
                bottom_left = append_meta(bottom_left, "SINGLE_EXPTIME", suffix = "s")
            bottom_left += "]"

        bottom_left_text = font.render(bottom_left, True, (255, 255, 255))
        bottom_left_rect = bottom_left_text.get_rect()
        bottom_left_rect.bottomleft = (10, h - 10)
        surface.blit(bottom_left_text, bottom_left_rect)

        # icons here might be nicer
        top_right = ""
        if saved:
            top_right = tab(top_right) + "SAVED"
        if paused:
            top_right = tab(top_right) + "PAUSED"

        if top_right:
            text = font.render(top_right, True, (255, 255, 255))
            rect = text.get_rect()
            rect.topright = (w - 10, 10)
            surface.blit(text, rect)

class Menu:
    def __init__(self, epaper, output_dir, camera, camera_settings, wheel, wheel_settings, mode):
        if not mode or (mode is Mode.INTERVALS and not camera_settings.intervals):
            mode = Mode.SINGLE

        surface = pygame.display.get_surface()
        w = surface.get_width()
        h = surface.get_height()

        self.epaper = epaper
        self.view = View(w, h)
        self.zoom = False # used to capture BACK
        if not camera:
            self.capture = None
            self.menu = None
            self.view.message("no camera")
            return

        self.capture = Capture(self.view, output_dir, camera, camera_settings, wheel, wheel_settings, mode)
        self.capture.start()

        self.screensaver = False

        self.menu = luddcam_settings.mk_menu("Capture")
        self.menu_active = False

        def select_mode(a, mode):
            print(f"setting mode to {mode}")
            self.capture.set_mode(mode)

        items = [("Single", Mode.SINGLE), ("Repeat", Mode.REPEAT)]
        if camera_settings.intervals:
            items.append(("Intervals", Mode.INTERVALS))

        self.menu.add.selector(
            "Mode: ",
            items=items,
            default=mode.value,
            onchange=select_mode,
            align=ALIGN_LEFT)

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

        if self.menu_active:
            for event in events:
                if is_action(event) or is_back(event):
                    self.menu_active = False

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
                    self.menu_active = True
                else:
                    # back is now screensaver
                    self.screensaver = True
                    print("blanking screen")
                    screen.fill((0, 0, 0))
                    backlight_off()
                    self.epaper.sleep()
            elif is_action(event):
                # TODO zoom should be changed to a two stage process: first
                # click shows a box, arrows move around, second click goes in
                # (arrows still work). The settings should persist per camera.
                # we need a way to either reset or (preferred) visually
                # highlight that we're dead on center, maybe by showing a faded
                # version of the center view.
                self.zoom = self.view.toggle_zoom()
            # TODO LEFT/RIGHT could be used to PAUSE and go to playback from disk

        if not self.screensaver:
            self.view.blit(screen)

# zoom means to crop to the target size
def downscale(mono, target_width, target_height, zoom, bayer):
    height, width = mono.shape
    if target_height > height or target_width > width:
        raise ValueError(f"downscale doesn't upscale ({mono.shape} => ({h},{w}))")

    #print(f"CAPTURE: {mono.shape}")

    # this can be surprisingly computationally expensive if we are not careful,
    # so we aim for speed above quality.
    #
    # we first throw away as much data as possible by cropping to the zoom
    # region or downsampling and then padding to the aspect ratio.
    #
    # then we use a fast debayer (no interpolation) for osc.
    #
    # with what remains we scale the image evenly, and then quantise into 8
    # bits. quantisation can be really slow if we do it too early. We use basic
    # median / max values of the scaled images instead of using percentiles on
    # the original, which gives us some protection against hot pixels.
    #
    # we always have to be mindful of resizing mono data to even numbers to
    # preserve the bayer pattern.

    s = 2 if bayer else 1
    if zoom:
        height, width = mono.shape
        startx = even_down(width // 2 - s * target_width // 2)
        starty = even_down(height // 2 - s * target_height // 2)
        mono = mono[starty:starty + s*target_height, startx:startx + s*target_width]
        #print(f"DOWNSAMPLE: image is now {mono.shape}")
    else:
        mono = down_sample_mono(mono, bayer, target_width, target_height)
        #print(f"DOWNSAMPLE: image is now {mono.shape}")
        mono = pad_to_aspect(mono, bayer, target_width, target_height)
        #print(f"LETTERBOX: image is now {mono.shape}")

    #print(f"CROPPED: {mono.shape}")

    # now debayer and downscale by slicing, then crop again
    if bayer:
        channels = debayer(mono, bayer)
        # print(f"BEBAYERED: {channels.shape}")
    else:
        channels = mono

    # now quantise to 8 bits
    if channels.dtype == np.uint8:
        channels_8 = channels
    else:
        # start = time.perf_counter()
        sampled = channels[::2, ::2] # speeds things up
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
            m = 255.0 / (hi - lo)
            # we need to go to 32 bit to handle negative values.
            #
            # we tould do something like apply a mask to channels to remove the
            # values that would wrap around and avoid recreating.
            channels_8 = ((channels.astype(np.int32) - lo) * m).clip(0, 255).astype(np.uint8)
        else:
            channels_8 = (channels >> 8).astype(np.uint8)

    #channels_8 = resize_nn(channels_8, target_width, target_height)
    channels_8 = resize_nn_pillow(channels_8, target_width, target_height)
    # print(f"RESIZED: {channels_8.shape}")

    if bayer:
        return channels_8
    else:
        return np.stack([channels_8] * 3, axis=-1)

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
    return img[ys[:, None], xs[None, :]]

def resize_nn_pillow(img, tw, th):
    pil = Image.fromarray(img)
    resized = pil.resize((tw, th), Image.NEAREST)
    return np.array(resized, dtype=img.dtype)

def crop_to_aspect(mono, bayer, target_width, target_height):
    height, width = mono.shape
    ar = width / height
    target_ar = target_width / target_height
    #print(f"we have {ar}, we aim for {target_ar}")
    if ar < target_ar:
        # like fitting VHS into widescreen, crop the height
        new_height = even_up(int(width * target_ar))
        sy = even_down((height - new_height) // 2)
        mono = mono[sy:sy + new_height, :]
    elif target_ar < ar:
        # like playing widescreen on VHS, crop the width
        new_width = even_up(int(height * target_ar))
        sx = even_down((width - new_width) // 2)
        mono = mono[:, sx:sx + new_width]
    return mono

# lossless way to fit it into the screen. we have previously
# cropped to aspect but it loses data. See below
def pad_to_aspect(mono, bayer, target_width, target_height):
    height, width = mono.shape
    ar = width / height
    target_ar = target_width / target_height

    if ar < target_ar:
        # like VHS into widescreen, add padding left/right
        new_width = even_up(int(width * target_ar))
        pad = (new_width - width) // 2
        # print(f"rescaling {ar} to {target_ar} with {pad} padding")
        mono = np.pad(mono, ((0, 0), (pad, pad)), mode="constant")
    elif ar > target_ar:
        # like widescreen on CRT, letterbox add padding top/bottom
        new_height = even_up(int(height * target_ar))
        pad = (new_height - height) // 2
        # print(f"rescaling {ar} to {target_ar} with {pad} padding")
        mono = np.pad(mono, ((pad, pad), (0, 0)), mode="constant")

    return mono

# given a target size this will throw away entire rows and columns
# to get into the right ballpark, retaining bayer cells.
def down_sample_mono(mono, bayer, target_width, target_height):
    height, width = mono.shape
    if width >= target_width and height >= target_height:
        step = min(width // (target_width), height // (target_height))
        if not bayer and step >= 2:
            mono = mono[::step, ::step]
        elif bayer and step >= 4:
            step = step // 2
            # print(f"downsampling bayered image by {step}")
            blk = mono.reshape(height // 2, 2, width // 2, 2)
            blk = blk[::step, :, ::step, :]
            ys, _, xs, _ = blk.shape
            mono = blk.reshape(ys * 2, xs * 2)
    return mono

# debayer the mono image using the given pattern, using fast downsampling.
#
# debayering algorithms that aim for quality rather than performance keep the
# original image size and fill in missing pixels by averaging in each channel.
def debayer(data, bayer):
    # print(f"debayering an {data.shape} image with {bayer} pattern")
    h, w = data.shape
    if h % 2 or w % 2:
        raise ValueError(f"debayer_simple expects even dimensions (got {h},{w})")

    dt = data.dtype

    tl = data[0::2, 0::2]
    tr = data[0::2, 1::2]
    #bl = data[1::2, 0::2]
    #br = data[1::2, 1::2]

    # def avg(a, c):
    #     return ((a.astype(np.uint16) + c.astype(np.uint16)) >> 1).astype(dt)

    # pick one of the greens and throw the other away for perf
    if bayer == "RGGB":
        #R, G, B = tl, avg(tr, bl), br
        br = data[1::2, 1::2]
        R, G, B = tl, tr, br
    elif bayer == "BGGR":
        # R, G, B = br, avg(tr, bl), tl
        br = data[1::2, 1::2]
        R, G, B = br, tr, tl
    elif bayer == "GRBG":
        # R, G, B = tr, avg(tl, br), bl
        bl = data[1::2, 0::2]
        R, G, B = tr, tl, bl
    elif bayer == "GBRG":
        # R, G, B = bl, avg(tl, br), tr
        bl = data[1::2, 0::2]
        R, G, B = bl, tl, tr
    else:
        raise ValueError(f"Unsupported Bayer pattern: {bayer}")

    return np.stack((R, G, B), axis=-1)

def lut_asinh(k):
    x = np.linspace(0, 1, 256)
    y = np.arcsinh(k * x) / np.arcsinh(k)
    return (y * 255).astype(np.uint8)

asinh_lut = lut_asinh(15)

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
                     colour=(255,255,255)):
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
    f = "test_data/sony_a7iii/m31/exposures/10.fit.fz"
    # actually a 30 second rgb exposure
    # f = "test_data/sony_a7iii/m31/exposures/1.fit.fz"
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
        "STAGE": Stage.CAPTURE,
        "MODE": Mode.REPEAT
    }
    out = "/foo/bar/IMG_00001.fits"
    font = pygame.font.Font(luddcam_settings.hack, 14)

    start = time.perf_counter()
    render_frame_for_screen(surface, img_raw, zoom, meta, out, font, False, False)
    end = time.perf_counter()
    print(f"rendering took {end - start:.2f}")

    pygame.image.save(surface, "test.png")
    # Image.fromarray(out).save("test.png")

    subprocess.Popen(["feh", "test.png"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
