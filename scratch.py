# scratch to test out https://sep.readthedocs.io/en/stable/tutorial.html

import numpy as np
import sep
import fitsio
import time

import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.patches import Ellipse

import luddcam_capture

#rcParams['figure.figsize'] = [10., 8.]

#f = "test_data/osc/exposures/1.fit.fz"
#fits = fitsio.FITS(f)[1]

f = "~/Astronomy/guiding/2025-09-21/Light_FOV_3.0s_Bin1_20250921-220316_0055.fit"
fits = fitsio.FITS(f)[0]

h = fits.read_header()
bayer = h.get("BAYERPAT")
if bayer and h.get("ROWORDER") != 'BOTTOM-UP':
    bayer = bayer[2:4] + bayer[0:2]

channels = np.flipud(fits.read())
if bayer:
    debayered = luddcam_capture.debayer(channels, bayer)
    data = debayered @ np.array([0.299, 0.587, 0.114])
else:
    data = np.array(channels).astype(np.float32)

# show the image
# m, s = np.mean(data), np.std(data)
# plt.imshow(data, interpolation='nearest', cmap='gray', vmin=m-s, vmax=m+s, origin='lower')
# plt.colorbar()
# plt.show()

# do we really need to extract the background here?
# maybe enough to use the median
start = time.perf_counter()
bkg = sep.Background(data)
end = time.perf_counter()
print(f"bkg computation took {end-start}")
print(bkg.globalback)
print(bkg.globalrms)

# plt.imshow(np.array(bkg), interpolation='nearest', cmap='gray', origin='lower')
# plt.colorbar()
# plt.show()

data_sub = data - bkg

#plt.imshow(np.array(data_sub), interpolation='nearest', cmap='gray', origin='lower')
#plt.colorbar()
#plt.show()

start = time.perf_counter()
objects = sep.extract(data_sub, 1.5, err=bkg.globalrms)
end = time.perf_counter()
print(f"extract took {end-start}")
print(len(objects))

fig, ax = plt.subplots()
m, s = np.mean(data_sub), np.std(data_sub)
im = ax.imshow(data_sub, interpolation='nearest', cmap='gray',
               vmin=m-s, vmax=m+s, origin='lower')

for i in range(len(objects)):
    e = Ellipse(xy=(objects['x'][i], objects['y'][i]),
                width=6*objects['a'][i],
                height=6*objects['b'][i],
                angle=objects['theta'][i] * 180. / np.pi)
    e.set_facecolor('none')
    e.set_edgecolor('red')
    ax.add_artist(e)
plt.show()
