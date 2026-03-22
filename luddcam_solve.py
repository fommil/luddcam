# the plate and polar alignment solver
#
# the polar alignment algorithm is using brute force search, we could use a more
# sophisticated approach that solves in a single step but noting that the normal
# of a plane is the pole, c.f.
# https://www.ilikebigbits.com/2015_03_04_plane_from_points.html

import datetime
import math
from math import sin, cos
import time

import erfa
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
def plate_solve(hints, centroids, width, height, scale_factor, pixel_size, polar_align, full_catalog):
    if hints is None or len(centroids) < 10:
        return None

    # if the number of "merged" centroids is too high then consider skipping for
    # a few frames since this is an indicator that the scope is being slewed.
    merged = np.sum((centroids['flag'] & sep.OBJ_MERGED) != 0) / len(centroids)
    if merged > 0.5:
        print(f"noteworthy number of merged stars {merged}")

    scale_hint = hints.pixscale
    if scale_hint is None:
        scale_hint = (scale_factor * 0.5, None)
    if polar_align is False:
        # if we're doing alignment, drop the RA hint
        pos_hint = (None, hints.dec_center)
    else:
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
                dsos = luddcam_catalog.relevant_dsos(full_catalog, dec_min, dec_max, ra_min, ra_max)
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
                current = (hints.ra_center, hints.dec_center)
                # show the alignment target
                if hints.align_targets is None:
                    pole, soln = find_pole(hints.align_samples, current)
                    hints.align_targets = (current, pole)
                    hints.align_error = soln

                # TODO it would be good to update the align_error to give a
                # realtime estimate of how close we are but that seems to
                # involve a full resolve (or at the very least some cos/sin
                # calculations).

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
    print(f"[find_pole] input {samples} {current}")
    cost = alignment_cost_function(samples)

    limits = 5 # in degrees in each direction
    samples = 60 # size of grid
    arcsec = 1.0 / (60 * 60)

    # we could use a local search here but it's not particularly reliable.
    # thankfully the cost function is cheap enough to justify brute force. we
    # progressively zoom in on the area of interest until the change is sub
    # arcsec.
    soln = (0, 0)
    for i in range(5):
        start = time.perf_counter()
        last = soln
        soln = global_search(cost, soln, limits, samples)
        end = time.perf_counter()
        print(f"[find_pole] global_search {i + 1} took {end - start} ({soln})")
        limits = 2 * limits / samples
        if np.linalg.norm(np.array(last) - np.array(soln)) < arcsec:
            break

    ra_err, dec_err = soln

    # it is not accurate to simply add the soln to the current, we have to
    # translate properly. This is because a degree near the pole is a lot
    # smaller than a degree at the equator.
    #
    # Also recall that t transforms the actual celestial sphere to where
    # our mount is, to fix our mount we need to do the opposite!
    t = rot3d(dec_err, ra_err, 0)
    target = xyz_to_radec(t.T @ radec_to_xyz(current))

    print(f"[find_pole] output {target} {soln}")

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

def global_search(cost, center, bound, precision):
    grid = np.zeros((precision, precision))
    yaws = np.linspace(center[0]-bound, center[0]+bound, precision)
    pitches = np.linspace(center[1]-bound, center[1]+bound, precision)
    for i, yaw in enumerate(yaws):
        for j, pitch in enumerate(pitches):
            grid[i, j] = cost((yaw, pitch))

    i, j = np.unravel_index(grid.argmin(), grid.shape)

    # uncomment to visualise the solution space
    # print(f"global solution seems to be at {(yaws[i], pitches[j])}")
    # import matplotlib.pyplot as plt
    # plt.imshow(grid)
    # from matplotlib.patches import Circle
    # circle = Circle((i, j), radius=1.5, fill=False, edgecolor='red', linewidth=2)
    # plt.gca().add_patch(circle)
    # plt.show()

    return yaws[i], pitches[j]

def mk_precession_matrix(d):
    return erfa.pmat06(*erfa.cal2jd(d.year, d.month, d.day))
precession = mk_precession_matrix(datetime.date.today())

# if precess is true this will convert to local RA/DEC coordinates from J2000
def radec_to_xyz(radec, precess=True):
    ra, dec = radec
    theta = math.radians(ra)
    phi = math.radians(90 - dec) # zero is the z axis
    xyz = np.array([cos(theta) * sin(phi), sin(theta) * sin(phi), cos(phi)])
    if precess:
        return precession @ xyz
    else:
        return xyz

# if precess is true this will convert from local RA/DEC coordinates to J2000
def xyz_to_radec(xyz, precess=True):
    if precess:
        xyz = precession.T @ xyz
    x, y, z = xyz
    dec = 90 - math.degrees(math.acos(np.clip(z, -1, 1)))
    ra = math.degrees(math.atan2(y, x)) % 360
    return ra, dec

# returns a lambda that takes a numpy array of [RA, DEC, DEC']
# where the first two are the pole correction and DEC' is the
# declination of the samples.
#
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
    # star = (88.792939, 7.407064)
    # print(xyz_to_radec(radec_to_xyz(star), precess=False))

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

    # more examples, these failed to improve alignment with the 3d pole algo.
    # the last two are perhaps the most relevant as they were near the equator
    # and I nailed the proposed solution.

    # [find_pole] [(184.557711523, 47.3020338355), (178.235992085, 47.3791030271), (170.667077006, 47.4670672177), (163.117970443, 47.5454474438), (146.36495886, 47.6824432972), (128.671207202, 47.7562182034), (111.501960914, 47.7670589104), (97.7230865444, 47.7174193781), (88.9719322237, 47.7786354833)] (88.9748514261, 47.7784066009)
    # [find_pole] [(88.9499967145, 48.396557105), (95.1762884536, 48.3133429989), (101.791005933, 48.2292487112), (111.877176828, 48.0691647484), (124.119858794, 47.8321937786), (137.787261386, 47.5503543383), (141.010841692, 47.4776045032), (173.711049973, 46.6838659719)] (173.654684786, 46.6933137019)


    # [find_pole] [(200.739602408, 3.70775359332), (204.836343627, 3.64802468801), (168.244560521, 4.0064205949), (158.856207586, 4.04078968189), (150.390625329, 4.04239287409), (142.018016536, 4.01864147753), (135.696397212, 3.988203533), (130.48048423, 3.94586253894)] (130.479615497, 3.94490821295)
    samples = [(200.739602408, 3.70775359332),
               (204.836343627, 3.64802468801),
               (168.244560521, 4.0064205949),
               (158.856207586, 4.04078968189),
               (150.390625329, 4.04239287409),
               (142.018016536, 4.01864147753),
               (135.696397212, 3.988203533),
               (130.48048423, 3.94586253894)]
    current = (130.479615497, 3.94490821295)
    # luddcam 5c338e8 solved to target=(130.44296577933886, 4.91442094258386) soln=(0.9595943474615415, 0.4545582876243784)
    # [find_pole] [(130.400230128, 4.89536566237), (135.832218965, 5.02427225774), (141.783431457, 5.10666137249), (149.733590213, 5.23237013664), (158.464473757, 5.32406206281), (169.198019603, 5.36463750117), (175.082318828, 5.34956701518), (180.373773852, 5.31400182341), (200.231228693, 4.99777581708), (205.573798767, 4.87363711428)] (205.57296167, 4.87467448314)

    samples = [(130.400230128, 4.89536566237),
               (135.832218965, 5.02427225774),
               (141.783431457, 5.10666137249),
               (149.733590213, 5.23237013664),
               (158.464473757, 5.32406206281),
               (169.198019603, 5.36463750117),
               (175.082318828, 5.34956701518),
               (180.373773852, 5.31400182341),
               (200.231228693, 4.99777581708),
               (205.573798767, 4.87363711428)]
    current = (205.57296167, 4.87467448314)
    # luddcam 5c338e8 solved to target=(205.70118015043897, 6.634966914527325) soln=(2.1717173758383503, 0.4545600080502908)
    # which is away from the original soln by almost a degree in RA

    # another data set, that didn't converge in the field, suspected because the
    # search algorithm is poor and we don't take precession into account. This
    # was version 62a2f5c.

    # [find_pole] [(77.0129270207, 7.11885531219), (84.7890260388, 7.14939796301), (92.8590046434, 7.1696578711), (99.8603025848, 7.1763687924), (108.199464863, 7.17293732834), (116.690588354, 7.15214458513), (145.231878072, 6.98185969509), (150.258348017, 6.93657467203), (152.872766537, 6.9103064465)] (152.873403343, 6.91049351625)
    # [find_pole] target=(152.80367006786076, 6.429840986711312) soln=(0.15153338765141927, 0.7575632050129316)
    # after precession and better search, this moves to
    # target = (152.80176532596045, 6.610639771226104)

    # [find_pole] [(152.811686844, 6.38869471026), (148.867409319, 6.4131583176), (144.670568951, 6.433679568), (142.139461756, 6.45062456808), (121.060689604, 6.56704355527), (114.382892454, 6.6028124481), (106.811262961, 6.6402299675), (97.6968577775, 6.68374370799), (91.9143064265, 6.71059420249), (84.4219594629, 6.73860369112), (73.4884060134, 6.77053768166), (68.1310237622, 6.78246794205)] (68.1288933993, 6.78221356056)
    # [find_pole] target=(68.1077756279273, 6.547492082493449) soln=(-0.2525300741689015, 0.15152225382281395)
    # [find_pole] [(68.1410783118, 6.53915364929), (76.0664517961, 6.45351610358), (82.1436285159, 6.40498751457), (91.8303275601, 6.32762766555), (102.788445621, 6.24979640912), (114.426258281, 6.17530571333), (120.91005868, 6.13931660834), (146.897089826, 6.02865670514), (152.487930346, 6.01727166971), (156.485737255, 6.00942316008)] (156.485373553, 6.00974170218)
    # [find_pole] target=(156.48132694052862, 6.486996715623789) soln=(-0.4545538577307046, -0.15151050140696357)

    samples = [(77.0129270207, 7.11885531219),
               (84.7890260388, 7.14939796301),
               (92.8590046434, 7.1696578711),
               (99.8603025848, 7.1763687924),
               (108.199464863, 7.17293732834),
               (116.690588354, 7.15214458513),
               (145.231878072, 6.98185969509),
               (150.258348017, 6.93657467203),
               (152.872766537, 6.9103064465)]
    current = (152.873403343, 6.91049351625)

    target, soln = find_pole(samples, samples[-1])
    print(f"target = {target}")

    fig = plt.figure()
    ax = fig.add_subplot(projection='3d')
    ax.set_axis_off()

    # our sample data and its naive circle
    samples_xyz = np.array([radec_to_xyz(rd) for rd in samples])
    ax.scatter(samples_xyz[:, 0], samples_xyz[:, 1], samples_xyz[:, 2], c="red", s=20)
    dec_ = float(np.mean(np.array(samples)[:,1]))
    bad_circle = np.array([radec_to_xyz(rd) for rd in [(ra, dec_) for ra in range(360)]])
    ax.scatter(bad_circle[:, 0], bad_circle[:, 1], bad_circle[:, 2], c="red", s=1)

    # current mount pole
    ax.scatter([0], [0], [1], c='red', s=100)

    # some ra / dec lines, a bit ugly but it's just for testing
    for dec in range(0, 90, 5):
        n = 60 if dec % 15 == 0 else 15
        sphere = np.array([radec_to_xyz((ra, dec)) for ra in np.linspace(0, 360, n)])
        ax.scatter(sphere[:, 0], sphere[:, 1], sphere[:, 2], c='grey', s=1)

    # TODO add some named stars

    # the solution / celestial pole
    ra_err, dec_err = soln
    t = rot3d(dec_err, ra_err, 0)
    pole = np.array(t @ [0, 0, 1])
    ax.scatter(pole[0], pole[1], pole[2], c='blue', s=100)
    target_ = radec_to_xyz(target)
    ax.scatter(target_[0], target_[1], target_[2], c="blue", s=20)

    # we have the transform but we actually don't know which dec fits our
    # original data to plot it, this calculates it
    dec_fit = dec_
    dec_err = None
    for d in np.linspace(dec_ - 5, dec_ + 5, 100):
        fit = np.array([t @ radec_to_xyz((ra, d)) for ra, _ in samples])
        err = np.mean((fit - samples_xyz) ** 2)
        if dec_err is None or err < dec_err:
            dec_err = err
            dec_fit = d

#    dec_fit = dec_ - target[1] + 1 # hack
    fit_circle = np.array([t @ radec_to_xyz(rd) for rd in [(ra, dec_fit) for ra in range(360)]])
    ax.scatter(fit_circle[:, 0], fit_circle[:, 1], fit_circle[:, 2], c="blue", s=1)

    ax.set_aspect('equal')
    plt.show()

# Local Variables:
# compile-command: "python3 luddcam_solve.py"
# End:
