# helper methods for dealing with images

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

import fitsio
import numpy as np
import pygame

import pygame_menu

import luddcam_astrometry
import luddcam_catalog

# TODO compression is actually quite slow, so maybe make this a setting at some
# point if it can be justified. Compression is usually a little less than 50% so
# it's hard to justify the cost on a battery powered rpi. Maybe worth it for
# long term storage. Even taking images every 10 seconds for 8 hours, an imx585
# would consume 50gb of space, so it seems better to just stick with
# uncompressed generally.
compression_enabled = False

WHITE=(255, 255, 255)
BLACK=(0, 0, 0)

def tab(s):
    if len(s) == 0:
        return s
    if not s.endswith(" "):
        s += " "
    tab = 4
    need = (-len(s)) % tab
    #print(f"adding {need} spaces to '{s}'")
    return s + " " * need

def tab_append_lookup(meta, s, key, prefix, suffix, align):
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

def format_dms(degrees):
    negative = degrees < 0
    degrees = abs(degrees)

    d = int(degrees)
    rem = (degrees - d) * 60
    m = int(rem)
    s = round((rem - m) * 60)

    res = "-" if negative else ""
    if d > 0 or not (m or s):
        res += f"{d}°"
    if s or m:
        res += f"{m}'"
    if s > 0:
        res += f"{s}\""
    return res

# this takes the centroids of an image that is potentially scaled down for display
#
# e.g. if a raw image is reduced 8x for display, scale_factor should be set to 8.
def plate_solve(hints, centroids, width, height, scale_factor, pixel_size, polar_align):
    if hints is None or len(centroids) < 10:
        return False, None, None, None, None

    scale_hint = hints.pixscale
    if scale_hint is None:
        scale_hint = (scale_factor * 0.5, None)
    pos_hint = (hints.ra_center, hints.dec_center)
    parity_hint = hints.parity

    relevant_stars, relevant_dsos, polar_alignment_points, polar_alignment_targets = None, None, None, None
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
            case ((ra1, dec1), target):
                ras = [(ra, dec1) for ra in np.linspace(ra_min, ra_max, 100)]
                polar_alignment_points = [tuple(a) for a in solver.radec_to_pixels(ras)]
                match target:
                    case None:
                        pass
                    case (ra2, dec2):
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


# Downscales the (bayered) image to fit within the target width/height returning
# an rgb image (retaining the original bittype) and an (optional) float32 mono
# variant.
#
# this can be surprisingly computationally expensive if we are not careful,
# so we aim for speed above quality. General rescaling with nearest neighbour
# turned out to be pretty bad quality (and certainly not worth the CPU).
#
# zoom means to crop to the target size (uses higher quality debayer)
#
# Tries to return a mono equivalent if possible.
def downscale(mono, target_width, target_height, zoom, bayer, quality = False):
    height, width = mono.shape
    if target_height > height or target_width > width:
        raise ValueError(f"downscale doesn't upscale ({mono.shape} => ({height},{width}))")

    if zoom:
        startx = even_down(width // 2 - target_width // 2)
        starty = even_down(height // 2 - target_height // 2)
        mono = mono[starty:starty + target_height, startx:startx + target_width]
        if bayer:
            # doesn't downsample
            return debayer_quality(mono, bayer), mono
    else:
        if bayer:
            # downsamples
            rgb = debayer_fast(mono, bayer)
            if quality:
                return pixel_bin(rgb, target_width, target_height), None
            return pixel_sample(rgb, target_width, target_height), None
        else:
            if quality:
                mono = pixel_bin(mono, target_width, target_height)
            else:
                mono = pixel_sample(mono, target_width, target_height)

    return np.stack([mono] * 3, axis=-1), mono

# bins (averages) pixels until the img fits within the target size
# this looks good but is much slower than sampling.
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

# much faster alternative to pixel_bin that throws data away
def pixel_sample(img, target_width, target_height):
    height, width = img.shape[:2]
    if width > target_width or height > target_height:
        img = img[0::2, 0::2]
        return pixel_sample(img, target_width, target_height)
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

# we write the data to the file, and then callback view.save with the filename
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

def save_fits(out, view, data, metadata, background = False):
    # print(f"...saving to {out}")
    writer = FitsWriter(view, data, out, metadata)
    if background:
        writer.start()
    else:
        writer.run()

have_fpack = shutil.which("fpack") is not None

# returns the image (right way up) and headers.
# be sure to use `get_corrected_bayer` to decode the bayer.
def load_fits(f):
    if f.endswith("z"):
        fits = fitsio.FITS(f)[1]
    else:
        fits = fitsio.FITS(f)[0]

    # bottom-up becomes top-down
    img = np.flipud(fits.read())
    h = fits.read_header()
    return img, h

def get_corrected_bayer(h):
    bayer = h.get("BAYERPAT")
    if bayer and h.get("ROWORDER") != 'BOTTOM-UP':
        return bayer[2:4] + bayer[0:2]
    return bayer
