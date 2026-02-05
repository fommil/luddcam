# scratch to test out https://sep.readthedocs.io/en/stable/tutorial.html

import csv
import fitsio
import numpy as np
from pathlib import Path
import pygame
import sep
import shutil
import subprocess
import tempfile
import time

import luddcam_capture
import luddcam_settings
import luddcam_catalog
import luddcam_astrometry

pygame.font.init()

#f = "test_data/osc/exposures/1.fit.fz"
f = "~/Astronomy/guiding/2025-09-21/Light_FOV_3.0s_Bin1_20250921-220316_0055.fit"

if f.endswith(".fz"):
    fits = fitsio.FITS(f)[1]
else:
    fits = fitsio.FITS(f)[0]

h = fits.read_header()
bayer = h.get("BAYERPAT")
if bayer and h.get("ROWORDER") != "BOTTOM-UP":
    bayer = bayer[2:4] + bayer[0:2]

raw = np.flipud(fits.read())
orig_height, orig_width = raw.shape

rgb, data = luddcam_capture.downscale(raw, orig_width // 4, orig_height // 4, False, bayer, True)

# difference between stretching before vs after. After seems best
#rgb = luddcam_capture.quantize(rgb, True)

rgb = luddcam_capture.quantize(rgb, True)

objects = luddcam_astrometry.source_extract(data)
height, width = data.shape

with luddcam_astrometry.Astrometry() as solver:
    bounds = solver.solve_field(objects, width, height)
    print(bounds)
    ra_min = bounds["ramin"]
    ra_max = bounds["ramax"]
    ra_center = bounds["ra_center"]
    dec_min = bounds["decmin"]
    dec_max = bounds["decmax"]
    dec_center = bounds["dec_center"]
    pixscale = bounds["pixscale"]

    print(f"found solution at {ra_center},{dec_center}")
    print(f"pixel scale is {pixscale}")

    print(f"resolving with the hints")
    bounds = solver.solve_field(objects, width, height, (ra_center, dec_center), (pixscale * 0.99, pixscale * 1.01))

    # just testing that method, not actually used
    radecs = solver.pixels_to_radec([(width // 2, height // 2), (0,0), (width, height), (width, 0), (0, height)])
    for o in radecs:
        print(f"RA/DEC={o}")

    print(f"reference DEC = {dec_center}, between RA {ra_min} -> {ra_max}")
    fixed_dec = np.column_stack([np.linspace(ra_min, ra_max, 100), np.full(100, dec_center)])
    alignment_dec = solver.radec_to_pixels(fixed_dec)

    relevant_stars = solver.with_radec_to_pixels(luddcam_catalog.relevant_stars(dec_min, dec_max, ra_min, ra_max))
    relevant_dsos = solver.with_radec_to_pixels(luddcam_catalog.relevant_dsos(dec_min, dec_max, ra_min, ra_max))

rgb_height, rgb_width,_ = rgb.shape

surface = pygame.Surface((rgb_width, rgb_height))
rgb = np.transpose(rgb, (1, 0, 2))
pygame.surfarray.blit_array(surface, rgb)

white = (255, 255, 255)

pygame.draw.lines(surface, white, closed=False, points=alignment_dec, width=1)

font = pygame.font.Font(luddcam_settings.hack, 18)
for dso in relevant_stars:
    #print(dso)
    # adding 0.5 to improve rounding
    x = dso["x"] + 0.5
    y = dso["y"] + 0.5
    text = font.render(dso["name"], True, white)
    surface.blit(text, (x + 10, y - text.get_height() // 2))


def draw_labelled_dso(surface, dso, pixscale, mark = None):
    width, height = surface.get_size()

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

    text = font.render(label, True, white)
    tx = cx - text.get_width() // 2
    ty = cy - text.get_height() // 2
    # clamp to screen
    tx = max(4, min(tx, width - text.get_width() - 4))
    ty = max(4, min(ty, height - text.get_height() - 4))
    surface.blit(text, (tx, ty))

    # the position circle is only useful for very big things
    radius_px = max(4, (dso.get("diameter", 0) * 60) / (2 * pixscale))
    if radius_px > text.get_width() // 2:
        pygame.draw.circle(surface, white, (x, y), radius_px, width=1)

arrows = {
    (-1, -1): "↖", (0, -1): "↑", (1, -1): "↗",
     (-1, 0): "←",                (1, 0): "→",
     (-1, 1): "↙",  (0, 1): "↓",  (1, 1): "↘"
}
def direction_indicator(x, y, width, height):
    dx = 1 if x >= width else (-1 if x < 0 else 0)
    dy = 1 if y >= height else (-1 if y < 0 else 0)
    return arrows.get((dx, dy))

width, height = surface.get_size()
in_frame = []
out_frame = {}
margin = 50

for dso in relevant_dsos:
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
    draw_labelled_dso(surface, dso, pixscale)

for mark, (dso, _) in out_frame.items():
    draw_labelled_dso(surface, dso, pixscale, mark)

pygame.image.save(surface, "test.png")
subprocess.Popen(["feh", "--force-aliasing", "test.png"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Local Variables:
# compile-command: "python3 scratch.py"
# End:
