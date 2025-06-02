#!/usr/bin/env python3

from enum import IntEnum
import os
import pathlib
import subprocess
import sys
import traceback
import warnings

import numpy as np
import pygame
import pygame_menu
import pygame_menu.controls as ctrl

import luddcam_settings
import zwo

from luddcam_settings import is_left, is_right, is_up, is_down, is_menu, is_start, is_button

APP_PATH = str(pathlib.Path(__file__).parent.resolve())
FPS = 15

# general UX notes
#
# SELECT should swap between the last viewed menu and the current mode
# START should behave like the shutter
#
# # Live
#
# LEFT picks the capture mode (single, continuous, intervalometer, boost)
# UP turns off the display (any button turns it back on again)
# START starts / pauses the capture.
#       another start (in interval mode) will pick up exactly where it left off but is not persistent.
# A will zoom in (two levels of zoom). D pad moves the area around. This also defines
#   the boost area of interest.
# B when in the menu or live view will go to a menu of modes (e.g. live, playback, guiding)
#   within a mode this should typically behave like "back"
#
# live mode should show the key information, such as capture mode and gain/exposure/etc.
# live is optional, it may not play if the primary camera is not selected (we're guiding only) or
# is a DSLR shutter control.
#
# # Guide
#
# like live but selects the guide camera and has some markup with inferred RA/DEC and PSFs
# maybe even guiding stats.
# This code should also be available in a standalone rpi with no screen, just LED feedback.
#
# If we could count the pulses or just infer periods, we could also have PEC
# when there is no guide camera or synscan.

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
    BLANK = 0
    SETTINGS = 1
    LIVE = 2

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

    # always start in settings, so we have to ack the camera etc and forces a
    # save to disk when returning to live view.
    mode = Mode.SETTINGS
    last = Mode.LIVE
    def push(new_mode):
        nonlocal mode
        nonlocal last
        last = mode
        mode = new_mode
    def pop():
        nonlocal mode
        mode = last

    capturing = False
    bg = None

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
            elif mode > Mode.SETTINGS and is_up(event):
                print("blanking screen")
                push(Mode.BLANK)
            elif mode == Mode.SETTINGS and is_menu(event):
                print("exiting settings")
                settings_menu.save()
                pop()
            elif mode > Mode.SETTINGS and is_menu(event):
                print("entering settings")
                push(Mode.SETTINGS)

            # TODO START (NES_START) starts / pauses the capture
            # TODO A (SELECT) does primary mode action, e.g. live will zoom
            # TODO B (BACK) goes to modal choice (live, playback, guiding)
            # TODO LEFT gives primary mode option, e.g. live will select single, continuous, intervalometer
            # TODO RIGHT gives secondary mode option, e.g. show stats

        if mode == Mode.BLANK:
            surface.fill((0, 0, 0))
        elif mode == Mode.SETTINGS:
            settings_menu.update(events)
        elif mode == Mode.LIVE:
            if bg:
                surface.blit(bg, (0, 0))
            else:
                # maybe need a filler image here
                surface.fill((0, 0, 0))

        pygame.display.update()
        clock.tick(FPS)

        # FIXME let's implement LIVE mode fully, with a min and max limit on
        #       exposure and support pressing the shutter, which drops us into a
        #       reduced playback mode.

        # TODO do captures on a different thread
        if mode == Mode.LIVE:
            if (camera := settings_menu.camera):
                prefs = settings_menu.camera_settings()

                if not capturing:
                    gain = prefs.gain
                    print(f"temp is {camera.temp()}C")
                    if camera.is_cooled:
                        print(f"cooler is {camera.cooler()}%")
                    #print(f"starting a capture on {camera.name} with gain {gain}")
                    if camera.capture_start(gain, 1):
                        capturing = True
                else:
                    status = camera.capture_wait()
                    if status == 1:
                        pass
                    elif status == 2:
                        print("capture is complete, extracting image")
                        img = camera.capture_finish()
                        print(f"image of size {img.shape} extracted")
                        capturing = False

                        bg = background_from_array(img, surface.get_width(), surface.get_height())

                    else:
                        # TODO it might be worth noting about turning on power
                        # before the usb cable if this happens 3 or more times.
                        print(f"something went wrong with capture {status}")
                        capturing = False

def background_from_array(img_array, target_width, target_height):
    height, width = img_array.shape

    scale_w = width / target_width
    scale_h = height / target_height
    scale = max(scale_w, scale_h)

    new_w = int(width / scale)
    new_h = int(height / scale)

    img_ds = img_array[::int(scale), ::int(scale)]
    img_ds = img_ds[:new_h, :new_w]

    img_8bit = (img_ds >> 8).astype(np.uint8)
    img_rgb = np.stack([img_8bit]*3, axis=-1)

    return pygame.surfarray.make_surface(np.transpose(img_rgb, (1, 0, 2)))

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"Failed to run the main: {e}")
        traceback.print_exc()

# Local Variables:
# compile-command: "PYTHONUNBUFFERED=1 SDL_VIDEODRIVER=x11 SDL_AUDIODRIVER=dummy SDL_NOMOUSE=1 python3 luddcam.py 2>&1 | grep -v DETECT_AVX2"
# End:
