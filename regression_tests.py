import glob
import os
import numpy as np
import pygame
import pygame_menu.menu
import shutil
import sys
import subprocess
import traceback
import threading
import time

import mocks
import luddcam
import luddcam_settings

def nothing(s):
    t = s / mocks.warp
    time.sleep(max(t, 1.0 / luddcam.FPS + 0.01))

def key(k, i = 1):
    while i > 0:
        i -= 1
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, {"key": k}))
        nothing(1)

def up(i = 1):
    key(pygame.K_UP, i)

def down(i = 1):
    key(pygame.K_DOWN, i)

def left(i = 1):
    key(pygame.K_LEFT, i)

def right(i = 1):
    key(pygame.K_RIGHT, i)

def a(i = 1):
    key(pygame.K_RETURN, i)

def b(i = 1):
    key(pygame.K_BACKSPACE, i)

def select(i = 1):
    key(pygame.K_SPACE, i)

def start(i = 1):
    key(pygame.K_DELETE, i)

def quit():
    pygame.event.post(pygame.event.Event(pygame.QUIT))

def expect_images(n, retries=10):
    got = len(glob.glob(f"{mocks.output_dir()}/IMG_*.fit*"))
    if got < n and retries > 0:
        print("FAILED image count with retry")
        nothing(1)
        return expect_images(n, retries=retries-1)
    assert got == n, f"expected {n} fits files, got {got}"

def snap(name, tolerance=10, retries=5):
    f = f"test_data/{mocks.test_mode}/assertions/{name}.png"
    current = pygame.display.get_surface().copy()

    if not os.path.exists(f):
        pygame.image.save(current, f)
    else:
        ref = pygame.image.load(f).convert_alpha()
        diff = surfaces_diff(current, ref)
        if diff > tolerance:
            if retries > 0:
                # exact timing is flakey, this buys us some runway
                print("FAILED screenshot test with retry")
                nothing(1)
                return snap(name, tolerance, retries=retries-1)

            ff = f"test_data/{mocks.test_mode}/failures/{name}.png"
            pygame.image.save(current, ff)

            diffs = surface_with_diff(current, ref, tolerance)
            fff = f"test_data/{mocks.test_mode}/failures/{name}_diffs.png"
            # unfortunately this saves the most recent one, so can be fiddly
            pygame.image.save(diffs, fff)
            subprocess.Popen(["feh", fff], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raise AssertionError(f"image mismatch {f}: max diff {diff:.2f}")

# This is intended to be used to account for anti-aliasing
# and little differences like that, not for catching data.
def surfaces_diff(s1, s2):
    if s1.get_size() != s2.get_size():
        return float("inf")

    # 16 bit to avoid wrap-around
    a1 = pygame.surfarray.array3d(s1).astype(np.int16)
    a2 = pygame.surfarray.array3d(s2).astype(np.int16)

    diff = np.abs(a1 - a2)
    return float(diff.max())

# draws a new image highlighting the differences
def surface_with_diff(current, ref, threshold):
    a1 = pygame.surfarray.array3d(current).astype(np.int16)
    a2 = pygame.surfarray.array3d(ref).astype(np.int16)

    per_pixel_diff = (a1 - a2).mean(axis=2)
    negative_mask = per_pixel_diff < -threshold
    positive_mask = per_pixel_diff > threshold

    out = current.copy()
    px = pygame.surfarray.pixels3d(out)
    px[negative_mask] = (59, 76, 192)
    px[positive_mask] = (221, 132, 65)

    return out

# start the user input in a background thread
def run():
    luddcam.ready.wait()
    # print("starting test...")

    try:
        path = f"test_data/{mocks.test_mode}/script.py"
        code = open(path).read()
        compiled = compile(code, path, "exec")
        exec(compiled, globals(), locals())
    except Exception as e:
        traceback.print_exc()
    finally:
        quit()

if __name__ == '__main__':
    # make this an input parameter, have many tests
    mocks.test_mode = "sony_a7iii/m31"
    mocks.warp = 4.0

    if os.path.exists(luddcam_settings.SETTINGS_FILE):
        # tests require a clean environment to run, externally managed
        print(f"ERROR: {luddcam_settings.SETTINGS_FILE} exists", file=sys.stderr)
        sys.exit(1)

    os.makedirs(f"test_data/{mocks.test_mode}/assertions", exist_ok=True)

    shutil.rmtree(mocks.output_dir(), ignore_errors=True)
    os.makedirs(mocks.output_dir())

    shutil.rmtree(f"test_data/{mocks.test_mode}/failures", ignore_errors=True)
    os.makedirs(f"test_data/{mocks.test_mode}/failures")

    threading.Thread(target=run, daemon=True, name="Test").start()

    try:
        luddcam.main()
    except Exception as e:
        print(f"Failed to run the main: {e}")
        traceback.print_exc()

    time.sleep(1) # or we never exit

# Local Variables:
# compile-command: "./luddcam.sh test"
# End:
