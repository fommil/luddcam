import ctypes
from ctypes import c_int, c_char, c_long, c_ubyte, c_ulong, POINTER, Structure, byref
import time

# Load shared library
asi = ctypes.cdll.LoadLibrary('./libASICamera2.so.1.37')  # Adjust as needed

ASI_GAIN = 0
ASI_EXPOSURE = 1
ASI_BANDWIDTHOVERLOAD = 6
ASI_IMG_RAW16 = 2
ASI_EXP_SUCCESS = 2

class ASI_CAMERA_INFO(Structure):
    _fields_ = [
        ('Name', c_char * 64),
        ('CameraID', c_int),
        ('MaxHeight', c_int),
        ('MaxWidth', c_int),
        ('IsColorCam', c_int),
        ('BayerPattern', c_int),
        ('SupportedBins', c_int * 16),
        ('SupportedVideoFormat', c_int * 8),
        ('PixelSize', ctypes.c_double),
        ('MechanicalShutter', c_int),
        ('ST4Port', c_int),
        ('IsCoolerCam', c_int),
        ('IsUSB3Host', c_int),
        ('IsUSB3Camera', c_int),
        ('ElecPerADU', ctypes.c_float),
        ('BitDepth', c_int),
        ('IsTriggerCam', c_int),
        ('Unused', c_int * 16),
    ]

# Function prototypes
asi.ASIGetNumOfConnectedCameras.restype = c_int
asi.ASIGetCameraProperty.argtypes = [POINTER(ASI_CAMERA_INFO), c_int]
asi.ASIOpenCamera.argtypes = [c_int]
asi.ASIInitCamera.argtypes = [c_int]
asi.ASISetControlValue.argtypes = [c_int, c_int, c_long, c_int]
asi.ASISetROIFormat.argtypes = [c_int, c_int, c_int, c_int, c_int]
asi.ASISetStartPos.argtypes = [c_int, c_int, c_int]
asi.ASIStartExposure.argtypes = [c_int, c_int]
asi.ASIGetExpStatus.argtypes = [c_int, POINTER(c_int)]
asi.ASIGetDataAfterExp.argtypes = [c_int, POINTER(c_ubyte), c_ulong]

# Detect and open ASI1600MM
if asi.ASIGetNumOfConnectedCameras() <= 0:
    raise RuntimeError("No ASI cameras found")

info = ASI_CAMERA_INFO()
asi.ASIGetCameraProperty(byref(info), 0)
if b'1600MM' not in info.Name:
    raise RuntimeError("ASI1600MM Pro not found")

cam_id = 0
asi.ASIOpenCamera(cam_id)
asi.ASIInitCamera(cam_id)

# Configure settings
asi.ASISetControlValue(cam_id, ASI_EXPOSURE, 1000000, 0)  # 1 second
#asi.ASISetControlValue(cam_id, ASI_GAIN, 100, 0)
#asi.ASISetControlValue(cam_id, ASI_BANDWIDTHOVERLOAD, 40, 0)

# Set full resolution, no binning, 16-bit
width = info.MaxWidth
height = info.MaxHeight
asi.ASISetROIFormat(cam_id, width, height, 1, ASI_IMG_RAW16)
asi.ASISetStartPos(cam_id, 0, 0)

# Exposure
asi.ASIStartExposure(cam_id, 0)

status = c_int()
while True:
    asi.ASIGetExpStatus(cam_id, byref(status))
    print(f"status = {status.value}")
    if status.value == ASI_EXP_SUCCESS:
        break
    time.sleep(0.02)

print("capture complete")
asi.ASIStopExposure(cam_id)

# # Retrieve and save image
# buf_len = width * height * 2  # RAW16: 2 bytes per pixel
# buf = (c_ubyte * buf_len)()
# asi.ASIGetDataAfterExp(cam_id, buf, buf_len)



# with open("asi1600_capture.pgm", "wb") as f:
#     f.write(f"P5\n{width} {height}\n65535\n".encode())
#     f.write(bytearray(buf))

# print("Image saved as asi1600_capture.pgm")
