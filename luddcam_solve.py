# the plate and polar alignment solver

import math
from math import sin, cos

import numpy as np
import sep

import luddcam_astrometry
import luddcam_catalog
from luddcam_catalog import ra_diff

class SolverHints:
    def __init__(self):
        self.ra_center = None
        self.dec_center = None
        self.pixscale = None # of the image that was platesolved
        self.parity = None
        self.focal_length = None # informational from last solve
        self.fails = 0 # counts the number of fails in a row
        self.merged = 0 # counts of merged stars
        self.align_samples = [] # (ra,dec) pairs of samples slewed on RA axis only
        self.align_targets = None # or a two tuple of ra,dec of the probe and target
        self.align_error = None # the exact ra/dec translation error

class PlateSolution:
    def __init__(self, relevant_stars, relevant_dsos, polar_alignment_points, polar_alignment_targets):
        self.relevant_stars = relevant_stars
        self.relevant_dsos = relevant_dsos
        self.polar_alignment_points = polar_alignment_points
        self.polar_alignment_targets = polar_alignment_targets

# this takes the centroids of an image that is potentially scaled down for display
#
# e.g. if a raw image is reduced 8x for display, scale_factor should be set to 8.
#
# mutates "hints", returns an optional PlateSolution
def plate_solve(hints, centroids, width, height, scale_factor, pixel_size, polar_align):
    if hints is None or len(centroids) < 10:
        return None

    # if the number of "merged" centroids has jumped dramatically since the last
    # successful solve, then skip for a few frames since this is an indicator
    # that the scope is being slewed.
    merged = np.sum((centroids['flag'] & sep.OBJ_MERGED) != 0)
    if hints.merged and hints.merged * 2 < merged and hints.fails < 10:
        hints.fails += 1
        return None

    scale_hint = hints.pixscale
    if scale_hint is None:
        scale_hint = (scale_factor * 0.5, None)
    pos_hint = (hints.ra_center, hints.dec_center)
    parity_hint = hints.parity

    relevant_stars, relevant_dsos, polar_alignment_points, polar_alignment_targets = None, None, None, None
    with luddcam_astrometry.Astrometry() as solver:
        bounds = solver.solve_field(centroids, width, height, pos_hint, scale_hint, parity_hint)
        if not bounds and hints.ra_center and hints.dec_center:
            # if it still failed, try without the position hint
            bounds = solver.solve_field(centroids, width, height, None, scale_hint, parity_hint)
        if not bounds and hints.pixscale and hints.fails > 10:
            # only reset after 10 full failures in a row, which should be enough
            # time for the mount to settle if it has been slewed.

            # if this still fails, throw away some of the more aggressive hints.
            # so we don't get into an infinite loop when the camera changes.
            hints.pixscale = None
            hints.parity = None
            hints.focal_length = None # informational only
            if scale_hint or parity_hint:
                # one last go this time around
                bounds = solver.solve_field(centroids, width, height, None, None, None)
        if not bounds:
            hints.fails += 1
            return None
        hints.fails = 0
        hints.merged = merged
        #print(bounds)
        ra_min = bounds["ramin"]
        ra_max = bounds["ramax"]
        hints.ra_center = bounds["ra_center"]
        dec_min = bounds["decmin"]
        dec_max = bounds["decmax"]
        hints.dec_center = bounds["dec_center"]
        hints.pixscale = bounds["pixscale"]
        hints.parity = bounds["parity"]

        def existing_alignment_sample():
            for ra, _ in hints.align_samples:
                if abs(ra_diff(hints.ra_center, ra)) < 1:
                    return True
            return False

        if pixel_size:
            hints.focal_length = round((scale_factor * pixel_size / hints.pixscale) * 206.265)
            # print(f"focal_length = {focal_length}")

        print(f"plate solved at {hints.ra_center},{hints.dec_center} scale {hints.pixscale} with {hints.focal_length}mm")

        match polar_align:
            case None:
                # no polar alignment, show labels
                stars = luddcam_catalog.relevant_stars(dec_min, dec_max, ra_min, ra_max)
                dsos = luddcam_catalog.relevant_dsos(dec_min, dec_max, ra_min, ra_max)
                relevant_stars = solver.with_radec_to_pixels(stars)
                relevant_dsos = solver.with_radec_to_pixels(dsos)
            case False:
                # collecting data
                if not existing_alignment_sample():
                    hints.align_samples.append((hints.ra_center, hints.dec_center))

                # show the fixed DEC from the first sample
                if hints.align_samples:
                    _, dec1 = hints.align_samples[0]
                    ras = [(ra, dec1) for ra in np.linspace(ra_min, ra_max, 100)]
                    polar_alignment_points = [tuple(a) for a in solver.radec_to_pixels(ras)]

            case True:
                # show the alignment target
                if hints.align_targets is None:
                    probe = (hints.ra_center, hints.dec_center)
                    pole, align_error = find_pole(hints.align_samples, probe)
                    hints.align_targets = (probe, pole)
                    hints.align_error = align_error

                probe, pole = hints.align_targets
                if pole is not None:
                    polar_alignment_targets = [tuple(a) for a in solver.radec_to_pixels([probe, pole])]

    return PlateSolution(relevant_stars, relevant_dsos, polar_alignment_points, polar_alignment_targets)

# Returns a location (and the error) that, when the scope is pointed there
# through the mount's alt/az polar wedge, means that we are more likely to be
# polar aligned. Probably only works in the northern hemisphere (due to lack of
# testing, it should work in the south in theory).
#
# RA/DEC are spherical coordinates for a sphere that can be transformed to
# x,y,z space. The true pole is at +-90 degrees DEC which is the normal
# vector of the circle drawn by a fixed DEC.
#
# But our samples are not perfectly aligned. So we need to find the pole
# that they are pointing at. We can do this with simple gradient descent or
# a slightly fancier way that involves finding the plane of the points using
# eigenvalue decomposition:
#
# https://www.ilikebigbits.com/2015_03_04_plane_from_points.html
# https://pmc.ncbi.nlm.nih.gov/articles/PMC4890955/
#
# The normal of that plane points towards the mount's pole. If normalised,
# it is actually the x,y,z location of the pole, which can be converted
# back to RA,DEC space.
def find_pole(samples, current):
    print(f"[find_pole] {samples} {current}")

    cost = alignment_cost_function(samples)
    start = global_search(cost)
    soln = minimize(cost, start)
    ra_err, dec_err = soln

    # it is not accurate to simply add the soln to the current, we have to
    # translate properly. This is because a degree near the pole is a lot
    # smaller than a degree at the equator.
    t = rot3d(dec_err, ra_err, 0)
    target = xyz_to_radec(t @ radec_to_xyz(current))

    print(f"[find_pole] {target} {soln}")

    return target, soln

# build a rotation matrix with the given alpha/beta/gamma parameters in degrees.
#
# Standard Tait-Bryan conventions (alpha = yaw, beta = pitch, gamma = roll)
# https://en.wikipedia.org/wiki/Rotation_matrix#General_3D_rotations
def rot3d(alpha, beta, gamma):
    a = math.radians(alpha)
    b = math.radians(beta)
    c = math.radians(gamma)
    return np.array(
        [[cos(b) * cos(c), sin(a) * sin(b) * cos(c) - cos(a) * sin(c), cos(a) * sin(b) * cos(c) + sin(a) * sin(c)],
         [cos(b) * sin(c), sin(a) * sin(b) * sin(c) + cos(a) * cos(c), cos(a) * sin(b) * sin(c) - sin(a) * cos(c)],
         [-sin(b), sin(a) * cos(b), cos(a) * cos(b)]])

# simple way to find out where to start the minimiser search
def global_search(cost):
    grid = np.zeros((100, 100))
    yaws = np.linspace(-5, 5, 100)
    pitches = np.linspace(-5, 5, 100)
    for i, yaw in enumerate(yaws):
        for j, pitch in enumerate(pitches):
            grid[i, j] = cost((yaw, pitch))

    i, j = np.unravel_index(grid.argmin(), grid.shape)
    return yaws[i], pitches[j]

# incredibly simple and inefficient but it doesn't need to be fancy
def minimize(cost, params, lr=0.01, eps=1e-6, iters=1000, tol=1e-10):
    params = np.array(params, dtype=float)
    for _ in range(iters):
        grad = np.zeros_like(params)
        c = cost(params)
        for i in range(len(params)):
            e = np.zeros_like(params)
            e[i] = eps
            grad[i] = (cost(params + e) - cost(params - e)) / (2 * eps)
        params -= lr * grad
        if np.linalg.norm(grad * lr) < tol:
            break
    return tuple(float(x) for x in params)

def radec_to_xyz(radec):
    ra, dec = radec
    theta = math.radians(ra)
    phi = math.radians(90 - dec) # zero is the z axis
    return np.array([cos(theta) * sin(phi), sin(theta) * sin(phi), cos(phi)])

def xyz_to_radec(xyz):
    x, y, z = xyz
    dec = 90 - math.degrees(math.acos(np.clip(z, -1, 1)))
    ra = math.degrees(math.atan2(y, x)) % 360
    return ra, dec

# returns a lambda that takes a numpy array of
# [pitch_err, yaw_err] ~(RA, DEC)
# and returns the cost relative to a circle
# created at that point by rotating the celestial sphere.
#
# assumes all samples were gathered by slewing only the RA axis
def alignment_cost_function(samples):
    samples_xyz = np.array([radec_to_xyz(s) for s in samples])

    def cost(params):
        pole = rot3d(params[1], params[0], 0) @ np.array([0, 0, 1])
        # dot product of the pole and the samples, gives the cos angle.
        # when we have the right pole for the samples, the variance
        # is minimised.
        #dists = np.array([np.arccos(np.clip(pole @ s, -1, 1)) for s in samples_xyz])
        dists = np.array([pole @ s for s in samples_xyz])
        return np.var(dists)

    return cost

if __name__ == "__main__":
    import matplotlib.pyplot as plt

    # this extracts data from subs, and uses the time as the RA (only an approximation)
    # import datetime
    # import glob
    # from luddcam_images import *
    # hints = SolverHints()
    # start = None
    # samples = []
    # for f in sorted(glob.glob("tmp/guiding/*.fit.fz")):
    #     img, headers = load_fits(f)
    #     height, width = img.shape
    #     data = img.astype(np.float32)
    #     objs = luddcam_astrometry.source_extract(data, cull=50)
    #     solution = plate_solve(hints, objs, width, height, 1, headers.get("XPIXSZ"), None)
    #     if solution:
    #         epoch = datetime.fromisoformat(headers["DATE"]).timestamp()
    #         if not start:
    #             start = epoch
    #             ra = 0
    #         if start:
    #             ra = (epoch - start) * 360 / (60 * 60 * 24)
    #         samples.append((ra, hints.dec_center))

    # print(samples)
    # exit(1)

    # import csv
    # with open('tmp/samples.csv') as f:
    #     reader = csv.reader(f)
    #     samples = [(float(ra), float(dec)) for ra, dec in reader]
    # samples = samples[::100]

    # this extracts data from actual alignment frames
    # hints = SolverHints()
    # for f in sorted(glob.glob("tmp/align/*.fit.fz")):
    #     img, headers = load_fits(f)
    #     height, width = img.shape
    #     data = img.astype(np.float32)
    #     objs = luddcam_astrometry.source_extract(data, cull=50)
    #     solution = plate_solve(hints, objs, width, height, 1, headers.get("XPIXSZ"), False)
    # print(hints.align_samples)

    samples = [(146.863510433, 24.4932219664),
               (91.9389180732, 24.6341121843),
               (55.3696507467, 24.3101586984),
               (156.600466429, 24.4012276267)]

    target, soln = find_pole(samples, samples[-1])
    print(f"target = {target}")

    fig = plt.figure()
    ax = fig.add_subplot(projection='3d')

    # our sample data and its naive circle
    orig = np.array([radec_to_xyz(rd) for rd in samples])
    ax.scatter(orig[:, 0], orig[:, 1], orig[:, 2], c="red", s=20)
    dec_ = float(np.mean(np.array(samples)[:,1]))
    #sample_circle = [(ra, dec_) for ra in range(360)]
    #circle = np.array([radec_to_xyz(rd) for rd in sample_circle])
    #ax.scatter(circle[:, 0], circle[:, 1], circle[:, 2], c="red")

    # current mount pole
    ax.scatter([0], [0], [1], c='red', s=100)

    # some ra / dec lines, a bit ugly but it's just for testing
    sphere = np.array([radec_to_xyz((ra, dec)) for dec in np.linspace(-80, 80, 10) for ra in np.linspace(0, 360, 50)])
    ax.scatter(sphere[:, 0], sphere[:, 1], sphere[:, 2], c='grey', s=1)
    sphere = np.array([radec_to_xyz((ra, dec)) for dec in np.linspace(-80, 80, 20) for ra in np.linspace(0, 360, 10)])
    ax.scatter(sphere[:, 0], sphere[:, 1], sphere[:, 2], c='grey', s=2)

    # the solution / celestial pole
    ra_err, dec_err = soln
    t = rot3d(dec_err, ra_err, 0)
    pole = np.array(t @ [0, 0, 1])
    ax.scatter(pole[0], pole[1], pole[2], c='blue', s=100)
    target_ = radec_to_xyz(target)
    ax.scatter(target_[0], target_[1], target_[2], c="blue", s=20)

    ax.set_aspect('equal')

    plt.show()

# Local Variables:
# compile-command: "python3 luddcam_solve.py"
# End:
