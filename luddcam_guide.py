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
import threading
import time

import fitsio
import numpy as np
import pygame

import pygame_menu

import luddcam_settings
from luddcam_settings import is_left, is_right, is_up, is_down, is_menu, is_start, is_action, is_back, is_button
import luddcam_capture
from luddcam_capture import FitsWriter

class Guide:
    def __init__(self, view, output_dir, guide):
        self.view = view
        self.output_dir = output_dir
        self.guide = guide

        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True, name="Capture")
        self.stop = False

    # Must be called by the UI thread to start the worker.
    def start(self):
        return self.thread.start()

    def stop(self):
        with self.lock:
            self.stop = True

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
        while True:
            time.sleep(0.1)
            with self.lock:
                if self.stop:
                    self.guide.capture_stop()
                    break

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
                print(f"...saving to {out}")

                if capture_mode == Mode.SINGLE:
                    # this allows the single image to stay on the screen until
                    # the user presses BACK to unpause or START to take another.
                    self.set_stage(Stage.PAUSE)
                elif capture_mode == Mode.INTERVALS:
                    # this allows us to track where we got to and if we paused
                    # during this exposure, it'll restart at exactly this point.
                    with self.lock:
                        self.interval_idx = capture_interval_idx

                # note that fitsio seems to automatically set BZERO and BSCALE
                metadata = []
                metadata.append(("PROGRAM", "luddcam"))
                metadata.append(("DATE", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")))
                metadata.append(("EXPTIME", capture_exposure))
                if capture_slot != None:
                    name = self.wheel_settings.filters[capture_slot] or f"Slot {capture_slot + 1}"
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
                    metadata.append(("OFFSET", self.camera.offset))
                if self.camera.bayer:
                    metadata.append(("BAYERPAT", self.camera.bayer))

                writer = FitsWriter(self.view, data, out, metadata)
                writer.start()

        print(f"capture stopped for {self.camera.name}")

class Menu:
    def __init__(self, output_dir, guide):
        surface = pygame.display.get_surface()
        w = surface.get_width()
        h = surface.get_height()
