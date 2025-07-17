#!/usr/bin/env python3

from enum import IntEnum
import os
import pathlib
import subprocess
import sys
import traceback
import warnings

import pygame
import pygame_menu
import pygame_menu.controls as ctrl

import luddcam_settings
import luddcam_capture
import luddcam_guide
import zwo

from luddcam_settings import is_left, is_right, is_up, is_down, is_menu, is_start, is_action, is_back, is_button

ALIGN_LEFT=pygame_menu.locals.ALIGN_LEFT
ALIGN_RIGHT=pygame_menu.locals.ALIGN_RIGHT

APP_PATH = str(pathlib.Path(__file__).parent.resolve())
FPS = 15

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
    BLANK    = 0
    SETTINGS = 1
    CHOOSE   = 2
    CAPTURE  = 3
    GUIDE    = 4

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
    choose_menu.add.button("Guide", action=lambda: push(Mode.GUIDE), align=ALIGN_LEFT)

    while True:
        events = pygame.event.get()
        for event in events:
            if event.type in [pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED]:
                enable_joystick()
            elif event.type == pygame.QUIT:
                print("asked to QUIT")
                pygame.quit()
                sys.exit()
            elif mode == Mode.BLANK and is_button(event):
                print("waking screen")
                pop()
            elif mode > Mode.CHOOSE and is_up(event):
                print("blanking screen")
                push(Mode.BLANK)
            elif mode == Mode.SETTINGS and is_menu(event):
                print("exiting settings")
                settings_menu.save()
                capture_menu = luddcam_capture.Menu(
                    settings_menu.output_dir(),
                    settings_menu.camera,
                    settings_menu.camera_settings(),
                    settings_menu.wheel,
                    settings_menu.wheel_settings())
                guide_menu = luddcam_guide.Menu(
                    settings_menu.output_dir(),
                    settings_menu.guide
                )
                pop()
            elif mode > Mode.SETTINGS and is_menu(event):
                print("entering settings")
                # TODO warning / ack about ending capture sessions
                #
                # TODO preserve capture mode
                capture_menu.cancel()
                guide_menu.cancel()
                push(Mode.SETTINGS)
            elif mode > Mode.CHOOSE and is_back(event):
                print("showing submenu choice")
                push(Mode.CHOOSE)
            elif mode == Mode.CHOOSE and is_back(event):
                print("exiting choice")
                pop()

        if mode == Mode.BLANK:
            surface.fill((0, 0, 0))
        elif mode == Mode.SETTINGS:
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
    except Exception as e:
        print(f"Failed to run the main: {e}")
        traceback.print_exc()
