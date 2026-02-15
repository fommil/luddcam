from pathlib import Path
import fitsio
import numpy as np
import os
import sep
import subprocess
import tempfile
import time

# TODO make this a setting and allow it to be tuned e.g. for the hemisphere and
# night's RA or month, it definitely speeds things up. the default value could
# be the current month from the untrustworthy clock that might be reasonably up
# to date.
#
# can be northern, southern, or empty
#
# an even better optimisation here would be if we had the user's exact
# location and time, then we could limit the search to just the objects
# overhead within a tighter tolerance.
hemisphere = "northern" #os.environ.get('HEMISPHERE', '').lower()

# We prefer to do the source extraction in python so that we don't
# have to deal with large fits files on disk just to interact with
# astrometry, reducing the memory overheads significantly even if
# it means blocking the GIL.
#
# This can also be used for guiding and focusing (minimising 'a')
# without having to plate solve.
#
# https://github.com/sep-developers/sep/issues/172
def source_extract(data):
    start = time.perf_counter()
    bkg = sep.Background(data)
    end = time.perf_counter()
    print(f"bkg computation took {end-start}")
    #print(bkg.globalback)
    #print(bkg.globalrms)
    data_sub = data - bkg
    start = time.perf_counter()
    # higher threshold with disabled kernel speeds things up
    objects = sep.extract(data_sub, 5, err=bkg.globalrms, filter_kernel=None)
    # sorted by descending flux
    end = time.perf_counter()
    print(f"extract took {end-start} for {len(objects)} objects")
    # then trim, to speed up plate solving significantly
    objects = objects[np.argsort(objects["flux"])[::-1]]
    objects = objects[:50]
    return objects

class Astrometry:
    def __init__(self):
        self.workdir = tempfile.mkdtemp()
        self.xyls = f"{self.workdir}/sources.xyls"
        self.wcs = f"{self.workdir}/sources.wcs"
        self.radec = f"{self.workdir}/radec.fits"
        self.pixels = f"{self.workdir}/pixels.fits"
        self.fields = ("ramin", "ramax", "decmin", "decmax", "ra_center", "dec_center", "pixscale", "parity")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for f in (self.xyls, self.wcs, self.radec, self.pixels):
             Path(f).unlink(missing_ok=True)
        Path(self.workdir).rmdir()

    # plate solve with the output from SEP, i.e. (x,y,flux) point sources.
    #
    # returns summary stats of the solution AND allows the coordinate
    # transformation methods of this class to be called (via a temp
    # sources.wcs file in the directory).
    #
    # pos hint can be None or a tuple of (ra, dec)
    # scale_hint can be None, or a number, or tuple of (lower, upper)
    # parity_hint can be 1, 0 (or None), -1
    def solve_field(self, objects, width, height, pos_hint = None, scale_hint = None, parity_hint = None):
        data = np.zeros(len(objects), dtype=[
            ("X", "f4"),
            ("Y", "f4"),
            ("FLUX", "f4")
        ])
        # FITS is 1 based, SEP is 0 based. Both refer to pixel centers
        data["X"] = objects["x"] + 1
        data["Y"] = objects["y"] + 1
        data["FLUX"] = objects["flux"]

        with fitsio.FITS(self.xyls, "rw", clobber=True) as fits:
            fits.write(data, header={"IMAGEW":width, "IMAGEH":height})

        scale = []
        if scale_hint:
            if isinstance(scale_hint, tuple):
                lower, upper = scale_hint
            else:
                lower, upper = (scale_hint * 0.95, scale_hint * 1.05)
            scale = ["--scale-low", str(lower), "--scale-high", str(upper)]

        position = []
        if pos_hint:
            ra, dec = pos_hint
            if ra is not None and dec is not None:
                # 10 degrees is enough to speed things up in most cases
                position = ["--ra", str(ra), "--dec", str(dec), "--radius", "10"]

        if not position:
            if hemisphere == "northern":
                position = ["--ra", "0", "--dec", "90", "--radius", "110"]
            elif hemisphere == "southern":
                position = ["--ra", "0", "--dec", "-90", "--radius", "110"]

        parity = []
        # this looks the wrong way around, we choose to agree with wcsinfo
        if parity_hint:
            if parity_hint > 0:
                parity = ["--parity", "neg"]
            elif parity_hint < 0:
                parity = ["--parity", "pos"]

        start = time.perf_counter()
        args = ["solve-field", self.xyls,
                "-D", self.workdir,
                *scale, *position, *parity,
                "--no-plots", "--no-verify", "--overwrite",
                "--scale-units", "arcsecperpix",
                "--corr", "none", "--match", "none",
                "--rdls", "none", "--solved", "none",
                "--new-fits", "none", "--index-xyls", "none",
                "--temp-axy",
                # these last parameters speed things up significantly
                "--no-remove-lines", "--uniformize", "0",
                # this speeds things up a tiny little bit
                "--no-tweak"]
        #print(" ".join(args))

        # delete any previous solutions so we can avoid stale results
        Path(self.wcs).unlink(missing_ok=True)

        try:
            subprocess.run(args,check=True, capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired:
            print(f"plate solving timed out")
            return None
        end = time.perf_counter()
        print(f"plate solving computation took {end-start}")

        if not os.path.exists(self.wcs):
            return None

        bounds = {}

        result = subprocess.run(
            ["wcsinfo", self.wcs],
            check=True, capture_output=True, text=True
        )
        #print(result.stdout)
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in self.fields:
                bounds[parts[0]] = float(parts[1])

        return bounds

    def pixels_to_radec(self, pixel_coords):
        pixel_coords = np.array(pixel_coords)
        if len(pixel_coords) == 0:
            return np.array([])

        data = np.zeros(len(pixel_coords), dtype=[
            ('X', 'f8'),
            ('Y', 'f8')
        ])
        # we need to add 1 to get into FITS "count from 1" convention
        data['X'], data['Y'] = (pixel_coords + 1).T

        with fitsio.FITS(self.pixels, "rw", clobber=True) as fits:
            fits.write(data)

        subprocess.run([
            'wcs-xy2rd',
            '-w', self.wcs,
            '-i', self.pixels,
            '-o', self.radec
        ], check=True, capture_output=True)

        radec_data = fitsio.read(self.radec)
        return np.column_stack([radec_data['RA'], radec_data['DEC']])

    def radec_to_pixels(self, radec_coords):
        radec_coords = np.array(radec_coords)
        if len(radec_coords) == 0:
            return np.array([])

        data = np.zeros(len(radec_coords), dtype=[
            ('RA', 'f8'),
            ('DEC', 'f8')
        ])
        data['RA'] = radec_coords[:, 0]
        data['DEC'] = radec_coords[:, 1]

        with fitsio.FITS(self.radec, "rw", clobber=True) as fits:
            fits.write(data)

        subprocess.run([
            'wcs-rd2xy',
            '-w', self.wcs,
            '-i', self.radec,
            '-o', self.pixels
        ], check=True, capture_output=True)

        pixels_data = fitsio.read(self.pixels)
        # subtract 1 to count from 0
        return np.column_stack([pixels_data['X'], pixels_data['Y']]) - 1

    # input is list of dictionaries, e.g. from a catalog
    def with_radec_to_pixels(self, data):
        with_pixels = self.radec_to_pixels([(o["ra"], o["dec"]) for o in data])
        return [
            {**obj, "x": x, "y": y}
            for obj, (x, y) in zip(data, with_pixels.tolist())
        ]
