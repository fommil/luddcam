import json
import os
import pathlib
import subprocess
import sys
import traceback
import warnings

from box import Box
import pygame

# NOTE: generally I've found pygame_menu to be quite fiddly to work with.
#
# The most annoying quirks are:
#
# 1. no built in tab based navigation mode, so it's been hard to build
#    the DSLR settings menu, and I've had to emulate one.
# 2. selectors must be rebuilt if their items change
# 3. selectors fire onchange multiple times to reach the default
# 4. doesn't seem to allow actions to create new menus
# 5. range_slider doesn't support gamepads
# 6. after a clear / rebuild a menu still renders the old version
#    until there's an event on it.
#
# and I'd be tempted to drop it in favour of something else. We might
# be ok using the default raspberry pi desktop, which would open up a
# lot more options. Basic tk is a reasonable option.

import pygame_menu
import pygame_menu.controls as ctrl

import zwo

ALIGN_LEFT=pygame_menu.locals.ALIGN_LEFT
ALIGN_RIGHT=pygame_menu.locals.ALIGN_RIGHT

# we assume that all removable media shows up here. We're intentionally
# limiting portability to Linux to simplify the code.
MEDIA_BASE = f"/media/{os.getlogin()}/"

# lets users optionally name their filter slots
FILTER_OPTIONS = ["undefined", "L", "R", "G", "B", "Sii", "Ha", "Oiii", "Ha Oiii", "Ha Oiii Hb", "Sii Oiii", "Dark" ]

# sensible DSO exposure times (might want to add planetary / flat / bias)
EXPOSURE_OPTIONS = [1, 2, 5, 10, 30, 60, 120, 180, 240, 300, 600, 900, 1200]

# sensible count of subs
FRAME_OPTIONS = [4, 6, 10, 12, 20, 25, 30, 50, 60, 100, 120, 1000]

# may want to move this to the media or user home dir at some point
SETTINGS_FILE = "luddcam-settings.json"

THEME = pygame_menu.themes.THEME_DARK
if (hack := pygame.font.match_font('hack')):
    THEME.title_font = hack
    THEME.widget_font = hack

# settings are the following shape.
#
# camera: { name: <str>, guide: <bool>, cooling: <bool>, gain_{min,max,default,unity}: <int> }
# guide: { name: <str>, guide: <bool>, cooling: <bool>, gain_{min,max,default,unity}: <int> }
# wheel: { name: <str>, slots: <int> }
# drive: <str> (relative to MEDIA_BASE)
#
# prefs: { <camera_name> : PREF }
# filters: { <wheel_name> : [<str> | null] }
#
# PREF := { cooling: <int>, gain: <int>, intervals: [ INTERVAL ] }
# INTERVAL := {exposure: <int>, frames: <int>, slot: <int> }
#
# There may appear to be some redundancy between camera, guide, wheel and
# prefs, but note that prefs is not guaranteed to exist. This may be
# changed in the future.
class Menu:
    def __init__(self):
        if os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                self.settings = Box(json.load(f), default_box=True)
        else:
            self.settings = Box(default_box=True)

        self.zwo_asi = zwo.AsiCamera2()
        self.zwo_efw = zwo.EfwFilter()
        self.cached_camera = None
        self.cached_wheel = None

        # I tried using a MenuLink to emulate tab behaviour, but it didn't work for the
        # kind of navigation we want where moving the joystick left/right when selecting
        # the title should move tab. It's more for selecting sub-menus.
        # https://pygame-menu.readthedocs.io/en/latest/_source/add_widgets.html#add-a-menu-link
        #
        # another design choice might be to destroy and rebuild the menu every time we change,
        # which would potentially simplify the code to populate dropdowns.
        self.choice = 0
        self.menus = []
        self.rebuild_menus()

    def save(self):
        print("saving settings")
        with open(SETTINGS_FILE, 'w') as f:
            d = self.settings.to_dict()
            json.dump(d, f, indent=2, sort_keys=True)

    def camera(self):
        camera = self.settings.camera
        if camera:
            if not self.cached_camera or self.cached_camera[0] != camera.name or not self.cached_camera[1]:
                print(f"creating new camera impl for {camera}")
                self.cached_camera = (camera.name, self.zwo_asi.find_camera(camera.name))
            return self.cached_camera[1]

    def wheel(self):
        wheel = self.settings.wheel
        if wheel:
            if not self.cached_wheel or self.cached_wheel[0] != wheel.name or not self.cached_wheel[1]:
                print(f"creating new wheel impl for {wheel}")
                self.cached_wheel = (wheel.name, self.zwo_efw.find_wheel(wheel.name))
            return self.cached_wheel[1]

    def get_prefs(self):
        camera = self.settings.camera
        if not camera:
            return
        prefs = self.settings.prefs.get(camera.name)

        if camera.cooling and prefs.cooling == {}:
            prefs.cooling = 0
        if not prefs.gain:
            prefs.gain = camera.gain_unity or camera.gain.gain_default
        if not prefs.intervals:
            prefs.intervals = []

        return prefs

    def get_filters(self):
        wheel = self.settings.wheel
        if not wheel:
            return
        filters = self.settings.filters.get(wheel.name)
        if not filters or len(filters) != wheel.slots:
            filters = [None] * wheel.slots
            self.settings.filters[wheel.name] = filters
        return filters

    def get_filter_name(self, i, no_gen = False):
        filters = self.get_filters()
        if not filters:
            return
        return filters[i]

    def set_filter_name(self, i, value = None):
        filters = self.get_filters()
        if not filters:
            return
        filters[i] = value

    # needs to be called any time settings are changed.
    # the option to skip rebuilding the devices menu allows
    # only quick cosmetic changes to be made in other menus.
    #
    # calls to this most likely implies that the app needs
    # to refresh something, but we may want to do that with
    # events or something.
    def rebuild_menus(self, skip_devices = False):
        if not self.menus:
            self.menus = [mk_menu() for i in range(4)]
        if not skip_devices:
            rebuild_menu(self.menus[0], self.mk_devices)
        rebuild_menu(self.menus[1], self.mk_filters)
        rebuild_menu(self.menus[2], self.mk_intervals)
        rebuild_menu(self.menus[3], self.mk_camera)

    def update(self, events):
        # left/right navigation should only work from the titles
        navigate = self.menus[self.choice].get_index() == 0

        for event in events:
            if not navigate:
                continue;
            if is_left(event):
                self.choice = max(0, self.choice - 1)
            elif is_right(event):
                self.choice = min(self.choice + 1, len(self.menus) - 1)

        menu = self.menus[self.choice]
        menu.update(events)
        menu.draw(pygame.display.get_surface())

    def format_drive(self):
        drive = self.settings.drive
        print(f"Formatting {drive}")
        mnt = run(f"findmnt --noheadings --output=SOURCE --target {MEDIA_BASE}{drive}")
        fs = run(f"lsblk --noheadings -o FSTYPE {mnt}")
        run(f"udevil unmount {mnt}")
        run(f"sudo /sbin/mkfs.{fs} {mnt}")
        run(f"udevil mount {mnt}")

    def mk_devices(self, menu):
        initialized = False
        # pygame_menu doesn't allow selector entries to be updated, so when any
        # of the items change we have to rebuild the whole menu.
        menu.clear()
        menu.add.button(f"Devices              1 / {len(self.menus)}", align=ALIGN_RIGHT)

        # the general approach here is: if you don't want to use something
        # then don't attach it. That way everything "just works" by default
        # if it is the only thing that is plugged in.
        zwo_cameras = self.zwo_asi.cameras()
        cameras = [a for a in zwo_cameras if not a.guide]
        guides = [a for a in zwo_cameras if a.guide]

        wheels = self.zwo_efw.wheels()

        if not cameras:
            button = menu.add.button("Camera: none", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            self.settings.camera = None
        else:
            def select_camera(a, camera):
                if not initialized:
                    return
                if self.settings.camera == camera:
                    return
                self.settings.camera = camera
                print(f"selected camera {camera}")
                self.rebuild_menus()
            camera = self.settings.camera
            default = cameras.index(camera) if camera and camera in cameras else 0
            self.settings.camera = cameras[default]
            items = [(a.name, a) for a in cameras]
            menu.add.selector(
                title="Camera: ",
                items=items,
                default=default,
                onchange=select_camera,
                align=ALIGN_LEFT)

        if not guides:
            button = menu.add.button("Guide: none", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            self.settings.guide = None
        else:
            def select_guide(a, guide):
                if not initialized:
                    return
                if self.settings.guide == guide:
                    return
                self.settings.guide = guide
                print(f"selected guide {guide}")
                # don't need to rebuild menus
            guide = self.settings.guide
            default = guides.index(guide) if guide and guide in guides else 0
            self.settings.guide = guides[default]
            items = [(a.name, a) for a in guides]
            menu.add.selector(
                title="Guide: ",
                items=items,
                default=default,
                onchange=select_guide,
                align=ALIGN_LEFT)

        if not wheels:
            button = menu.add.button("Filter Wheel: none", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            self.settings.wheel = None
        else:
            def select_wheel(a, wheel):
                if not initialized:
                    return
                if self.settings.wheel == wheel:
                    return
                self.settings.wheel = wheel
                print(f"selected wheel {wheel}")
                self.rebuild_menus()
            wheel = self.settings.wheel
            default = wheels.index(wheel) if wheel and wheel in wheels else 0
            self.settings.wheel = wheels[default]
            items = [(a.name, a) for a in wheels]
            menu.add.selector(
                title="Filter Wheel: ",
                items=items,
                default=default,
                onchange=select_wheel,
                align=ALIGN_LEFT)

        drives = os.listdir(MEDIA_BASE)
        if not drives:
            button = menu.add.button("Drive: none", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            self.settings.drive = None
        else:
            def select_drive(a):
                if not initialized:
                    return
                drive = a[0][0]
                if self.settings.drive == drive:
                    return
                self.settings.drive = drive
                print(f"selected drive {drive}")
            drive = self.settings.drive
            default = drives.index(drive) if drive and drive in drives else 0
            self.settings.drive = drives[default]
            menu.add.selector(
                title="Drive: ",
                items=tuples(drives),
                default=default,
                onchange=select_drive,
                align=ALIGN_LEFT)

            confirm_format = mk_menu(f"Confirm erase all data on {self.settings.drive}")
            confirm_format.add.button('No', pygame_menu.events.BACK)
            confirm_format.add.button('Yes', lambda m: (self.format_drive(), m._back()), confirm_format)
            menu.add.button("Format", action=confirm_format, align=ALIGN_LEFT)

        menu.add.button("Refresh", action=self.rebuild_menus, align=ALIGN_LEFT)
        initialized = True

    def mk_filters(self, menu):
        initialized = False
        menu.clear()
        menu.add.button(f"Filter Wheels           2 / {len(self.menus)}", align=ALIGN_RIGHT)

        if not self.settings.wheel or not self.settings.camera:
            button = menu.add.button("No slots", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            return

        # can't be defined inside the loop because python closures mess with
        # intuitive understanding of scope. This should also take the current
        # filter so that we can avoid misfires when refreshing the whole menu.
        def select_slot(i, a):
            if not initialized:
                return
            name = a[0][0]
            if name == FILTER_OPTIONS[0]:
                name = None
            if self.get_filter_name(i) == name:
                return
            self.set_filter_name(i, name)
            print(f"updated slot {i} to {name}")
            self.rebuild_menus(skip_devices = True)

        exclude = [f for f in self.get_filters() if f]
        for i in range(self.settings.wheel.slots):
            choice = self.get_filter_name(i) or FILTER_OPTIONS[0]
            options = [f for f in FILTER_OPTIONS if f == choice or f not in exclude]
            default = options.index(choice) if choice in options else 0
            menu.add.selector(
                title=f"Slot {i + 1}: ",
                items=tuples(options),
                default=default,
                onchange=lambda a, i=i: select_slot(i, a),
                align=ALIGN_LEFT)

        def calibrate():
            print("called calibrate")
            if (api := self.wheel()):
                api.calibrate()

        menu.add.button("Calibrate", action=calibrate, align=ALIGN_LEFT)
        initialized = True

    def mk_intervals(self, menu):
        initialized = False
        menu.clear()
        menu.add.button(f"Intervals              3 / {len(self.menus)}", align=ALIGN_RIGHT)

        if not self.settings.camera:
            button = menu.add.button("No camera", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            return

        # TODO load (some are stock templates)
        # TODO save (we can have 2 numbered slots, per hardware combo)

        def clear():
            self.get_prefs().intervals = []
            rebuild_menu(menu, self.mk_intervals)
        if not self.get_prefs().intervals:
            button = menu.add.button("Clear", clear, align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
        else:
            menu.add.button("Clear", clear, align=ALIGN_LEFT)

        # pygame_menu requires submenus to be constructed in advance
        # so we have to create a menu for every possible action.

        intervals = self.get_prefs().intervals
        if intervals:
            menu.add.label("-" * 32, font_size=10, align=ALIGN_LEFT)

        def filter_name(i):
            return self.get_filter_name(i) or f"Slot {i + 1}"

        for entry in intervals:
            # TODO clicking asks for edit / move / delete
            e = Box(entry)
            suf = ""
            if self.settings.wheel:
                suf = f" with {filter_name(e.slot)}"
            summary = ""
            total = int((e.frames * e.exposure) / 60)
            if total > 0:
                summary = f" ({total} minutes)"
            menu.add.button(f"{e.frames} x {e.exposure} secs{suf}{summary}", align=ALIGN_LEFT)

        if intervals:
            menu.add.label("-" * 32, font_size=10, align=ALIGN_LEFT)

        # TODO infer defaults for new entries from the existing ones
        new_entry = Box()
        new_entry.exposure = 60
        new_entry.frames = 20

        filter_choices = []
        if self.settings.wheel:
            new_entry.slot = 0
            for i in range(self.settings.wheel.slots):
                # we want to display the name, but use the slot number in the
                # config. Which means if the user changes the slot naming then
                # the intervalometer setup may need to be adjusted.
                filter_choices.append((filter_name(i), i))

        default_exposure = EXPOSURE_OPTIONS.index(new_entry.exposure)
        default_frames = FRAME_OPTIONS.index(new_entry.frames)
        default_filter = 0

        def select_exposure(a, b):
            if not initialized:
                return
            new_entry.exposure = b
            print(f"changing exposure in new entry to {a[0][0]}")

        def select_frames(a, b):
            if not initialized:
                return
            new_entry.frames = b
            print(f"changing frames in new entry to {a[0][0]}")

        def select_filter(a, b):
            if not initialized:
                return
            new_entry.slot = b
            print(f"changing filter to {a[0][0]} (idx {b})")

        def add():
            if not initialized:
                return
            self.get_prefs().intervals.append(new_entry)
            print(f"adding the new entry {new_entry}")
            # only impacts this menu
            rebuild_menu(menu, self.mk_intervals, count_from_end = True)

        menu_add = mk_menu("Add interval")
        menu_add.add.selector(
            "Frames (count): ",
            items=[(str(i), i) for i in FRAME_OPTIONS],
            default=default_frames,
            onchange=select_frames,
            align=ALIGN_LEFT)
        menu_add.add.selector(
            "Exposure (seconds): ",
            items=[(str(i), i) for i in EXPOSURE_OPTIONS],
            default=default_exposure,
            onchange=select_exposure,
            align=ALIGN_LEFT)
        if filter_choices:
            # print(f"using filters {filter_choices}")
            menu_add.add.selector(
                "Filter: ",
                items=filter_choices,
                default=default_filter,
                onchange=select_filter,
                align=ALIGN_LEFT)

        menu_add.add.button('Done', lambda m: (add(), m._back()), menu, align=ALIGN_LEFT)
        menu_add.add.button('Cancel', pygame_menu.events.BACK, align=ALIGN_LEFT)
        menu.add.button("Add", action=menu_add, align=ALIGN_LEFT)

        # TODO "repeat last N entries X times"
        # if self.intervals:
        #     menu.add.button("Repeat", action=menu_repeat, align=ALIGN_LEFT)

        initialized = True

    def mk_camera(self, menu):
        initialized = False
        menu.clear()
        menu.add.button(f"Camera              4 / {len(self.menus)}", align=ALIGN_RIGHT)

        if not self.settings.camera:
            button = menu.add.button("No camera", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
        else:
            if self.settings.camera.cooling:
                def update_cooling(a, cooling):
                    if not initialized:
                        return
                    if cooling == self.get_prefs().cooling:
                        return
                    self.get_prefs().cooling = cooling
                    print(f"changed target temp to {cooling}")

                # range_slider doesn't work with a gamepad...
                # https://github.com/ppizarror/pygame-menu/issues/478
                cooling = self.get_prefs().cooling
                options = list(range(-20, 20 + 1, 5))
                default = options.index(cooling)
                choices = [(f"{i}°C", i) for i in options]
                menu.add.selector(
                    "Target Temp: ",
                    items=choices,
                    default=default,
                    onchange=update_cooling,
                    align=ALIGN_LEFT)
            else:
                button = menu.add.button("No cooling", align=ALIGN_LEFT)
                button.update_font({"color": (100, 100, 100)})

            # TODO binning
            # TODO anti-dew heater

            # print(f"camera gain settings = {self.settings.camera.gain}")
            if self.settings.camera.gain_max:
                def update_gain(a, gain):
                    if not initialized:
                        return
                    if gain == self.get_prefs().gain:
                        return
                    self.get_prefs().gain = gain
                    print(f"changed camera gain to {gain}")
                gain = self.get_prefs().gain
                start = self.settings.camera.gain_min
                end = self.settings.camera.gain_max
                step = round((end - start) / 10)
                options = list(range(start, end + 1, step))
                if gain not in options:
                    options.append(gain)
                    options = sorted(set(options))
                choices = [(f"{i}", i) for i in options]
                default = options.index(gain)
                menu.add.selector(
                    "Gain: ",
                    items=choices,
                    default=default,
                    onchange=update_gain,
                    align=ALIGN_LEFT)

        initialized = True

# TODO live exposure

# TODO guiding settings
# gain
# exposure
# dithering

# TODO boost mode for planetary. Zoom box size.
# (capped frame count), with choice of enabling zoom

# TODO cheats
# (enabled with konami combo)
# enable plate solving (if installed)
# enable polar alignment (take two pictures rotated on the RA)
# enable sky quality measurement mode (exposure recommendation)
# enable automatic object tracking in boost mode
# enable autostretching in live / playback

# convenient want to shell out. If we want to make this portable, we'd need
# to find all uses of this and use portable python libraries instead of
# linux commands.
def run(cmd):
    print(f"DEBUG (run): {cmd}")
    return subprocess.run(cmd, shell=True, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()

# pygame_menu requires items to be lists of tuples
def tuples(strings):
    return [(s,) for s in strings]

def rebuild_menu(menu, fn, count_from_end = False):
    selected_idx = 0
    if menu.get_selected_widget():
        selected_idx = menu.get_widgets().index(menu.get_selected_widget())
    if count_from_end:
        selected_idx = len(menu.get_widgets()) - selected_idx
    fn(menu)
    if selected_idx > 0:
        print(f"recovering saved position at widget {selected_idx}")
        for widget in menu.get_widgets():
            idx = menu.get_widgets().index(widget)
            if count_from_end:
                idx = len(menu.get_widgets()) - idx
            if idx == selected_idx:
                menu.select_widget(widget)
                break

def mk_menu(title = "Settings"):
    surface = pygame.display.get_surface()
    return pygame_menu.Menu(title, surface.get_width(), surface.get_height(), center_content=False, theme=THEME)

def is_left(event):
    return ((event.type == pygame.JOYAXISMOTION and
             event.axis == ctrl.JOY_AXIS_X and
             event.value < -ctrl.JOY_DEADZONE) or
            (event.type == pygame.KEYDOWN and
             event.key == pygame.K_LEFT))

def is_right(event):
    return ((event.type == pygame.JOYAXISMOTION and
             event.axis == ctrl.JOY_AXIS_X and
             event.value > ctrl.JOY_DEADZONE) or
            (event.type == pygame.KEYDOWN and
             event.key == pygame.K_RIGHT))

def is_up(event):
    return ((event.type == pygame.JOYAXISMOTION and
             event.axis == ctrl.JOY_AXIS_Y and
             event.value < -ctrl.JOY_DEADZONE) or
            (event.type == pygame.KEYDOWN and
             event.key == pygame.K_UP))

def is_down(event):
    return ((event.type == pygame.JOYAXISMOTION and
             event.axis == ctrl.JOY_AXIS_Y and
             event.value > ctrl.JOY_DEADZONE) or
            (event.type == pygame.KEYDOWN and
             event.key == pygame.K_DOWN))

# the button labelled "SELECT" on a NES is used to access the settings menu
NES_SELECT = 8
def is_menu(event):
    return ((event.type == pygame.JOYBUTTONDOWN and
             event.button == NES_SELECT) or
            (event.type == pygame.KEYDOWN and
             event.key == pygame.K_SPACE))

# the button labelled "START" on a NES is used as the shutter
NES_START = 9
def is_start(event):
    return ((event.type == pygame.JOYBUTTONDOWN and
             event.button == NES_START) or
            (event.type == pygame.KEYDOWN and
             event.key == pygame.K_SPACE))

def is_button(event):
    return (event.type in [pygame.JOYBUTTONDOWN, pygame.KEYDOWN] or
            event.type == pygame.JOYAXISMOTION and
            abs(event.value) > ctrl.JOY_DEADZONE)
