import json
import os
import pathlib
import subprocess
import sys
import traceback
import warnings

from box import Box
import pygame

# NOTE: pygame_menu has some quirks:
#
# 1. no built in tab based navigation mode, so I've emulated one.
# 2. selectors must be rebuilt if their items change
# 3. selectors fire onchange multiple times to reach the default
# 4. doesn't seem to allow actions to create new menus, they
#    must be made in advance.
# 5. range_slider doesn't support gamepads
# 6. after a clear / rebuild a menu still renders the old version
# 7. selectors must take tuples, not even a list of strings.

import pygame_menu
import pygame_menu.controls as ctrl

import zwo

ALIGN_LEFT=pygame_menu.locals.ALIGN_LEFT
ALIGN_RIGHT=pygame_menu.locals.ALIGN_RIGHT

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
# camera: <str>
# guide: <str>
# wheel: <str>
# drive: <str> (relative to MEDIA_BASE)
#
# cameras: { <camera_name> : CAMERA }
# wheels: { <wheel_name> : [<str> | null] }
#
# CAMERA := { cooling: <int>, gain: <int>, intervals: [ INTERVAL ] }
# INTERVAL := {exposure: <int>, frames: <int>, slot: <int> }
class Menu:
    def __init__(self):
        if os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                self.settings = Box(json.load(f), default_box=True)
        else:
            self.settings = Box(default_box=True)

        self.zwo_asi = zwo.AsiCamera2()
        self.zwo_efw = zwo.EfwFilter()
        self.refresh = True

        # these are impls, not names. They should always match the settings
        # and are populated when we (re)build the menus.
        self.camera = None
        self.guide = None
        self.wheel = None
        # cameras, guides, wheels defined later which hold all the last scanned impls

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

    def camera_settings(self):
        if self.settings.camera:
            return self.settings.cameras[self.settings.camera]

    def wheel_settings(self):
        if self.settings.wheel:
            return self.settings.wheels[self.settings.wheel]

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
            self.menus[0] = rebuild_menu(self.menus[0], self.mk_devices)
        self.menus[1] = rebuild_menu(self.menus[1], self.mk_filters)
        self.menus[2] = rebuild_menu(self.menus[2], self.mk_intervals)
        self.menus[3] = rebuild_menu(self.menus[3], self.mk_capture)

    def rebuild_intervals(self):
        self.menus[2] = rebuild_menu(self.menus[2], self.mk_intervals)

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
        if sys.platform != "linux":
            print(f"ERROR: 'format' is not supported on {sys.platform}")
            return

        drive = self.settings.drive
        print(f"Formatting {drive}")
        mnt = run(f"findmnt --noheadings --output=SOURCE --target {MEDIA_BASE}{drive}")
        fs = run(f"lsblk --noheadings -o FSTYPE {mnt}")
        run(f"udevil unmount {mnt}")
        run(f"sudo /sbin/mkfs.{fs} {mnt}")
        run(f"udevil mount {mnt}")

    def mk_devices(self):
        initialized = False
        menu = mk_menu()
        menu.add.button(f"Devices              1 / {len(self.menus)}", align=ALIGN_RIGHT)

        # the general approach here is: if you don't want to use something then
        # don't attach it. That way everything "just works" by default if it is
        # the only thing that is plugged in.
        if self.refresh:
            print("refreshing ZWO hardware")
            zwo_cameras = self.zwo_asi.cameras()
            self.cameras = [a for a in zwo_cameras if not a.guide]
            self.guides = [a for a in zwo_cameras if a.guide]
            self.wheels = self.zwo_efw.wheels()
            self.refresh = False

        def set_camera(c):
            if c == self.camera:
                return
            if c == None:
                print("unsetting camera")
                self.settings.camera = None
                self.camera = None
                return
            print(f"set camera {c.name}")
            self.settings.camera = c.name
            self.camera = c
            if c.name == "none":
                return
            prefs = self.camera_settings()
            if c.is_cooled and prefs.cooling == {}:
                prefs.cooling = 0
            if c.has_gain and prefs.gain == {}:
                if c.gain_unity != None:
                    prefs.gain = c.gain_unity
                else:
                    prefs.gain = c.gain_default
            if prefs.intervals == {}:
                prefs.intervals = []
            # turn on cooling as soon as we can!
            if c.is_cooled:
                self.camera.set_cooling(prefs.cooling)
            if c.has_gain:
                self.camera.set_gain(prefs.gain)

        def set_guide(c):
            if c == self.guide:
                return
            if c == None:
                print("unsetting guide")
                self.settings.guide = None
                self.guide = None
                return
            print(f"set guide {c.name}")
            self.settings.guide = c.name
            self.guide = c
            if not c:
                return
            # we don't expose guide settings directly, but if the user ever
            # selected this guide camera as a main camera then there may be
            # preferences to take into account.
            if len(prefs := self.settings.cameras.get(c.name)) > 0:
                print(f"considering preferences for guide camera {prefs}")
                if c.is_cooled and prefs.cooling != {}:
                    self.guide.set_cooling(prefs.cooling)
                if c.has_gain and prefs.gain != {}:
                    self.guide.set_gain(prefs.gain)

        # we calculate default camera and guide first before rendering, so we
        # can allow guide cameras to appear in the main camera list. If we
        # have only a guide camera attached, preference is given to guiding
        # although it can be unselected and moved.

        guides = [a for a in self.guides if a != self.camera]
        if guides:
            guides.append(none_selected)
            if self.settings.guide:
                default_guide = find_index(guides, lambda c: c.name == self.settings.guide, 0)
            else:
                default_guide = 0
            set_guide(guides[default_guide])

        cameras = [a for a in self.cameras] + [a for a in self.guides if a != self.guide]
        if cameras:
            cameras.append(none_selected)
            if self.settings.camera:
                default_camera = find_index(cameras, lambda c: c.name == self.settings.camera, 0)
            else:
                default_camera = 0
            set_camera(cameras[default_camera])

        if not cameras:
            button = menu.add.button("Camera: none", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            set_camera(none_selected)
        else:
            def select_camera(a, camera):
                if not initialized or self.camera == camera:
                    return
                set_camera(camera)
                self.rebuild_menus()
            menu.add.selector(
                title="Camera: ",
                items=[(a.name, a) for a in cameras],
                default=default_camera,
                onchange=select_camera,
                align=ALIGN_LEFT)

        if not guides:
            button = menu.add.button("Guide: none", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            set_guide(none_selected)
        else:
            def select_guide(a, guide):
                if not initialized or self.guide == guide:
                    return
                set_guide(guide)
                self.rebuild_menus()
            menu.add.selector(
                title="Guide: ",
                items=[(a.name, a) for a in guides],
                default=default_guide,
                onchange=select_guide,
                align=ALIGN_LEFT)

        def set_wheel(w):
            if w == self.wheel:
                return
            if not w:
                print("unsetting wheel")
                self.settings.wheel = None
                self.wheel = None
                return
            print(f"set wheel {w.name}")
            self.settings.wheel = w.name
            self.wheel = w
            prefs = self.wheel_settings()
            if len(prefs.filters) != self.wheel.slots:
                prefs.filters = [None] * self.wheel.slots
        if not self.wheels:
            button = menu.add.button("Filter Wheel: none", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            set_wheel(None)
        else:
            def select_wheel(a, wheel):
                if not initialized or self.wheel == wheel:
                    return
                set_wheel(wheel)
                self.rebuild_menus()
            if self.wheel:
                default = find_index(self.wheels, lambda c: c.name == self.wheel.name, 0)
            else:
                default = 0
            set_wheel(self.wheels[default])
            menu.add.selector(
                title="Filter Wheel: ",
                items=[(a.name, a) for a in self.wheels],
                default=default,
                onchange=select_wheel,
                align=ALIGN_LEFT)

        def set_drive(d):
            if not d:
                print("unsetting drive")
                self.settings.drive = None
                return
            print(f"set drive {d}")
            self.settings.drive = d
        drives = list_drives()
        if not drives:
            button = menu.add.button("Drive: none", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            set_drive(None)
        else:
            def select_drive(a):
                drive = a[0][0]
                if not initialized or self.settings.drive == drive:
                    return
                set_drive(drive)
            default = find_index(drives, lambda c: c == self.settings.drive, 0)
            print(f"DEBUG DEFAULT DRIVE = {default} in {drives}")
            set_drive(drives[default])
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

        def select_refresh():
            self.refresh = True
            self.rebuild_menus()
        menu.add.button("Refresh", action=select_refresh, align=ALIGN_LEFT)
        initialized = True
        return menu

    def mk_filters(self):
        initialized = False
        menu = mk_menu()
        menu.add.button(f"Filter Wheels           2 / {len(self.menus)}", align=ALIGN_RIGHT)

        if not self.wheel:
            button = menu.add.button("No slots", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            return menu

        # can't be defined inside the loop because python closures mess with
        # intuitive understanding of scope. This should also take the current
        # filter so that we can avoid misfires when refreshing the whole menu.
        def select_slot(i, a):
            if not initialized:
                return
            name = a[0][0]
            if name == FILTER_OPTIONS[0]:
                name = None
            filters = self.wheel_settings().filters
            if filters[i] == name:
                return
            print(f"updating slot {i} to {name}")
            filters[i] = name
            self.rebuild_menus(skip_devices = True)

        filters = self.wheel_settings().filters
        for i in range(len(filters)):
            choice = filters[i] or FILTER_OPTIONS[0]
            options = [f for f in FILTER_OPTIONS if f == choice or f not in filters]
            default = options.index(choice) if choice in options else 0
            menu.add.selector(
                title=f"Slot {i + 1}: ",
                items=tuples(options),
                default=default,
                onchange=lambda a, i=i: select_slot(i, a),
                align=ALIGN_LEFT)

        # FIXME live / single / repeat filter default

        def calibrate():
            print("called calibrate")
            self.wheel.calibrate()
        menu.add.button("Calibrate", action=calibrate, align=ALIGN_LEFT)

        initialized = True
        return menu

    def mk_intervals(self):
        initialized = False
        menu = mk_menu()
        menu.add.button(f"Intervals              3 / {len(self.menus)}", align=ALIGN_RIGHT)

        if not self.camera:
            button = menu.add.button("No camera", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            return menu

        # TODO load (some are stock templates)
        # TODO save (we can have 2 numbered slots, per hardware combo)

        def clear():
            self.camera_settings().intervals = []
            rebuild_intervals()

        intervals = self.camera_settings().intervals
        if not intervals:
            button = menu.add.button("Clear", clear, align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
        else:
            menu.add.button("Clear", clear, align=ALIGN_LEFT)

        # pygame_menu requires submenus to be constructed in advance
        # so we have to create a menu for every possible action.

        if intervals:
            menu.add.label("-" * 32, font_size=10, align=ALIGN_LEFT)

        def filter_name(i):
            return self.wheel_settings().filters[i] or f"Slot {i + 1}"

        for entry in intervals:
            # TODO clicking asks for edit / move / delete
            e = Box(entry)
            suf = ""
            if self.wheel:
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
        if self.wheel:
            new_entry.slot = 0
            for i in range(self.wheel.slots):
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
            intervals.append(new_entry)
            print(f"adding the new entry {new_entry}")
            self.rebuild_intervals()

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

        # TODO repeat tickbox

        initialized = True
        return menu

    def mk_capture(self):
        initialized = False
        menu = mk_menu()
        menu.add.button(f"Capture              4 / {len(self.menus)}", align=ALIGN_RIGHT)

        if not self.camera:
            button = menu.add.button("No camera", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
            return menu

        if not self.camera.is_cooled:
            button = menu.add.button("No cooling", align=ALIGN_LEFT)
            button.update_font({"color": (100, 100, 100)})
        else:
            def update_cooling(a, cooling):
                if not initialized or cooling == self.camera_settings().cooling:
                    return
                self.camera_settings().cooling = cooling
                self.camera.set_cooling(cooling)
            # range_slider doesn't work with a gamepad...
            # https://github.com/ppizarror/pygame-menu/issues/478
            cooling = self.camera_settings().cooling
            options = list(range(-20, 20 + 1, 5))
            menu.add.selector(
                "Target Temp: ",
                items=[(f"{i}°C", i) for i in options],
                default=options.index(cooling),
                onchange=update_cooling,
                align=ALIGN_LEFT)

        # TODO binning
        # TODO anti-dew heater

        if self.camera.has_gain:
            def update_gain(a, gain):
                if not initialized or gain == self.camera_settings().gain:
                    return
                self.camera_settings().gain = gain
                self.camera.set_gain(gain)
                print(f"changed camera gain to {gain}")
            gain = self.camera_settings().gain
            start = self.camera.gain_min
            end = self.camera.gain_max
            step = round((end - start) / 10)
            options = list(range(start, end + 1, step))
            if self.camera.gain_unity:
                options.append(self.camera.gain_unity)
            options.append(self.camera.gain_default)
            options = sorted(set(options))
            menu.add.selector(
                "Gain: ",
                items=[(f"{i}", i) for i in options],
                default=options.index(gain),
                onchange=update_gain,
                align=ALIGN_LEFT)

        # FIXME single / repeat capture exposure

        initialized = True
        return menu

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

# takes the old menu, and a fn that creates a new one, preserving the old
# selection index. It is not enough to .clear() the old menu, we must rebuild it
# because pygame_menu will render the stale data and there's no (obvious) way to
# force a re-render.
def rebuild_menu(menu, fn, count_from_end = False):
    selected_idx = 0
    if menu.get_selected_widget():
        selected_idx = menu.get_widgets().index(menu.get_selected_widget())
    if count_from_end:
        selected_idx = len(menu.get_widgets()) - selected_idx
    menu = fn()
    if selected_idx > 0:
        print(f"recovering saved position at widget {selected_idx}")
        for widget in menu.get_widgets():
            idx = menu.get_widgets().index(widget)
            if count_from_end:
                idx = len(menu.get_widgets()) - idx
            if idx == selected_idx:
                menu.select_widget(widget)
                break
    return menu

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

# how is this not in the stdlib?
def find_index(lst, pred, default=None):
    return next((i for i, x in enumerate(lst) if pred(x)), default)

# assume removable media shows up here on Linux (i.e. devmon / udisk)
if sys.platform == "linux":
    MEDIA_BASE = f"/media/{os.getlogin()}/"
elif sys.platform == "darwin":
    MEDIA_BASE = "/Volumes/"

def list_drives():
    if sys.platform == "win32":
        import win32file
        return [
            f"{d}:\\" for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            if win32file.GetDriveType(f"{d}:\\") == win32file.DRIVE_REMOVABLE
        ]
    else:
        os.listdir(MEDIA_BASE)

def get_drive(relative):
    if sys.platform == "win32":
        return relative
    else:
        return f"{MEDIA_BASE}"

class NoCamera:
    def __init__(self):
        self.name = "none"
        #self.is_cooled = False
        #self.has_gain = False
    def __bool__(self):
        return False

none_selected = NoCamera()
