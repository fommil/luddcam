#!/usr/bin/env python3

from enum import IntEnum
import os
import pathlib
import subprocess
import sys
import threading
import time
import traceback
import warnings

import pygame
import pygame_menu
import pygame_menu.controls as ctrl

import luddcam_settings
import luddcam_capture
import luddcam_guide

from luddcam_settings import is_left, is_right, is_up, is_down, is_menu, is_start, is_action, is_back, is_button

ALIGN_LEFT=pygame_menu.locals.ALIGN_LEFT
ALIGN_RIGHT=pygame_menu.locals.ALIGN_RIGHT

APP_PATH = str(pathlib.Path(__file__).parent.resolve())
FPS = 10

# UX principals:
#
# SELECT toggles the settings menu (cancels captures)
# START is reserved for shutter
# BACK toggles the mode select (or exit a sub-mode)
#
# A and D-Pad are mode specific.

# must be kept alive or it is collected by GC and joystick dies
joystick = None
def enable_joystick():
    global joystick
    if pygame.joystick.get_count() > 0:
        joystick = pygame.joystick.Joystick(0)
        print(f"using joystick {joystick.get_name()}")
        if joystick.get_name() == "USB gamepad":
            # TODO might want to read these from a database, or ensure the user
            #      has the latest. Check this with a SNES controller as I think
            #      it might be the right way around by default.

            # change the default keys to match the NES controller, which seems
            # to have them the wrong way around. Apparent SELECT=2 and START=3.
            ctrl.JOY_BUTTON_SELECT = 1 # A
            ctrl.JOY_BUTTON_BACK = 0 # B
    else:
        print("No joystick detected.")

# bugs in kmsdrm mode mean the mouse is still visible even with SDL_NOMOUSE=1 so
# this works around that by making it transparent.
def disable_mouse():
    blank = pygame.Surface((8, 8), pygame.SRCALPHA)
    blank.fill((0, 0, 0, 0))
    blank_cursor = pygame.cursors.Cursor((0, 0), blank)
    pygame.mouse.set_cursor(blank_cursor)
    pygame.mouse.set_visible(False)

class Mode(IntEnum):
    SETTINGS = 0
    CHOOSE   = 1
    CAPTURE  = 2
    GUIDE    = 3

ready = threading.Event()

def main():
    pygame.display.init()
    pygame.font.init()
    pygame.joystick.init()

    pygame.display.set_caption("LuddCam")

    clock = pygame.time.Clock()
    pygame.init()

    if pygame.display.get_driver() == "x11":
        surface = pygame.display.set_mode((800, 600))
    else:
        surface = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)

    disable_mouse()

    settings_menu = luddcam_settings.Menu()
    capture_menu = None
    guide_menu = None

    # always start in settings, so we have to ack the camera etc and forces a
    # save to disk when returning to live view.
    mode = Mode.SETTINGS
    last = Mode.CAPTURE
    def push(new_mode):
        nonlocal mode
        nonlocal last
        last = mode
        mode = new_mode
    def pop():
        nonlocal mode
        mode = last

    choose_menu = luddcam_settings.mk_menu("Choose Mode")
    choose_menu.add.vertical_margin(10)
    choose_menu.add.button("Capture", action=lambda: push(Mode.CAPTURE), align=ALIGN_LEFT)
    # choose_menu.add.button("Guide", action=lambda: push(Mode.GUIDE), align=ALIGN_LEFT)

    capture_mode = None

    ready.set()

    while True:
        events = pygame.event.get()
        for event in events:
            # print(f"DEBUG: received event {event}")
            if event.type in [pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED]:
                enable_joystick()
            elif event.type == pygame.QUIT:
                print("asked to QUIT")
                if capture_menu:
                    capture_menu.cancel()
                if guide_menu:
                    guide_menu.cancel()
                # this can hang, so do it in the background
                quitter = threading.Thread(target=pygame.quit, daemon=True, name="quit")
                quitter.start()
                return
            elif mode == Mode.SETTINGS and is_menu(event):
                print("exiting settings")
                settings_menu.save()
                capture_menu = luddcam_capture.Menu(
                    settings_menu.output_dir(),
                    settings_menu.camera,
                    settings_menu.camera_settings(),
                    settings_menu.wheel,
                    settings_menu.wheel_settings(),
                    capture_mode)
                # guide_menu = luddcam_guide.Menu(
                #     settings_menu.output_dir(),
                #     settings_menu.guide
                # )
                pop()
            elif mode > Mode.SETTINGS and is_menu(event):
                print("entering settings")
                # TODO warning / ack about ending capture sessions
                if capture_menu:
                    capture_mode = capture_menu.get_mode()
                    # print(f"capture will recover with {capture_mode}")
                    capture_menu.cancel()
                if guide_menu:
                    guide_menu.cancel()
                push(Mode.SETTINGS)

        if mode == Mode.SETTINGS:
            settings_menu.update(events)
        elif mode == Mode.CHOOSE:
            choose_menu.update(events)
            choose_menu.draw(surface)
        elif mode == Mode.CAPTURE:
            capture_menu.update(events)
        elif mode == Mode.GUIDE:
            guide_menu.update(events)

        pygame.display.update()
        clock.tick(FPS)

if __name__ == '__main__':
    try:
        main()
        time.sleep(1) # or we never exit
    except Exception as e:
        print(f"Failed to run the main: {e}")
        traceback.print_exc()
