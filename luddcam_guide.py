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

import luddcam_settings
from luddcam_settings import is_left, is_right, is_up, is_down, is_menu, is_start, is_action, is_back, is_button
import luddcam_capture
from luddcam_capture import mk_metadata, save_fits, View
import mocks

class Stage(Enum):
    LIVE = 0
    START = 1
    PAUSE = 2
    STOP = 3

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
