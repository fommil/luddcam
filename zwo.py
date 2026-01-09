# https://indilib.org/forum/development/15497-zwo-unity-gain-and-offset-calculations.html

from ctypes import *
from enum import IntEnum
import itertools
import platform
import time

import numpy as np

class ASI_BAYER_PATTERN(IntEnum):
    ASI_BAYER_RG = 0  # RGGB
    ASI_BAYER_BG = 1
    ASI_BAYER_GR = 2
    ASI_BAYER_GB = 3

class ASI_IMG_TYPE(IntEnum):
    ASI_IMG_RAW8 = 0     # Each pixel is an 8-bit (1 byte) gray level
    ASI_IMG_RGB24 = 1    # Each pixel consists of RGB, 3 bytes totally (color cameras only)
    ASI_IMG_RAW16 = 2    # 2 bytes for every pixel with 65536 gray levels
    ASI_IMG_Y8 = 3       # monochrome mode，1 byte every pixel (color cameras only)
    ASI_IMG_END = -1

class ASI_GUIDE_DIRECTION(IntEnum):
    ASI_GUIDE_NORTH = 0
    ASI_GUIDE_SOUTH = 1
    ASI_GUIDE_EAST = 2
    ASI_GUIDE_WEST = 3

class ASI_FLIP_STATUS(IntEnum):
    ASI_FLIP_NONE = 0     # no flip
    ASI_FLIP_HORIZ = 1    # horizontal image flip
    ASI_FLIP_VERT = 2     # vertical image flip
    ASI_FLIP_BOTH = 3     # horizontal + vertical image flip

class ASI_CAMERA_MODE(IntEnum):
    ASI_MODE_NORMAL = 0
    ASI_MODE_TRIG_SOFT_EDGE = 1
    ASI_MODE_TRIG_RISE_EDGE = 2
    ASI_MODE_TRIG_FALL_EDGE = 3
    ASI_MODE_TRIG_SOFT_LEVEL = 4
    ASI_MODE_TRIG_HIGH_LEVEL = 5
    ASI_MODE_TRIG_LOW_LEVEL = 6
    ASI_MODE_END = -1

class ASI_ERROR_CODE(IntEnum):
    ASI_SUCCESS = 0                    # operation was successful
    ASI_ERROR_INVALID_INDEX = 1       # no camera connected or index value out of boundary
    ASI_ERROR_INVALID_ID = 2          # invalid ID
    ASI_ERROR_INVALID_CONTROL_TYPE = 3  # invalid control type
    ASI_ERROR_CAMERA_CLOSED = 4       # camera didn't open
    ASI_ERROR_CAMERA_REMOVED = 5      # failed to find the camera, maybe the camera has been removed
    ASI_ERROR_INVALID_PATH = 6        # cannot find the path of the file
    ASI_ERROR_INVALID_FILEFORMAT = 7
    ASI_ERROR_INVALID_SIZE = 8        # wrong video format size
    ASI_ERROR_INVALID_IMGTYPE = 9     # unsupported image format
    ASI_ERROR_OUTOF_BOUNDARY = 10     # the startpos is outside the image boundary
    ASI_ERROR_TIMEOUT = 11            # timeout
    ASI_ERROR_INVALID_SEQUENCE = 12   # stop capture first
    ASI_ERROR_BUFFER_TOO_SMALL = 13   # buffer size is not big enough
    ASI_ERROR_VIDEO_MODE_ACTIVE = 14
    ASI_ERROR_EXPOSURE_IN_PROGRESS = 15
    ASI_ERROR_GENERAL_ERROR = 16      # general error, eg: value is out of valid range
    ASI_ERROR_INVALID_MODE = 17       # the current mode is wrong
    ASI_ERROR_GPS_NOT_SUPPORTED = 18  # this camera do not support GPS
    ASI_ERROR_GPS_VER_ERR = 19        # the FPGA GPS ver is too low
    ASI_ERROR_GPS_FPGA_ERR = 20       # failed to read or write data to FPGA
    ASI_ERROR_GPS_PARAM_OUT_OF_RANGE = 21 # start line or end line out of range, should make them between 0 ~ MaxHeight - 1
    ASI_ERROR_GPS_DATA_INVALID = 22   # GPS has not yet found the satellite or FPGA cannot read GPS data
    ASI_ERROR_END = 23

class ASI_BOOL(IntEnum):
    ASI_FALSE = 0
    ASI_TRUE = 1

class ASI_CONTROL_TYPE(IntEnum):
    ASI_GAIN = 0                    # gain
    ASI_EXPOSURE = 1                # exposure time (microsecond)
    ASI_GAMMA = 2                   # gamma with range 1 to 100 (nominally 50)
    ASI_WB_R = 3                    # red component of white balance
    ASI_WB_B = 4                    # blue component of white balance
    ASI_OFFSET = 5              # pixel value offset (a bias, not a scale factor)
    ASI_BANDWIDTHOVERLOAD = 6       # the total data transfer rate percentage
    ASI_OVERCLOCK = 7               # over clock
    ASI_TEMPERATURE = 8             # sensor temperature,10 times the actual temperature
    ASI_FLIP = 9                    # image flip
    ASI_AUTO_MAX_GAIN = 10          # maximum gain when auto adjust
    ASI_AUTO_MAX_EXP = 11           # maximum exposure time when auto adjust，unit is microseconds
    ASI_AUTO_TARGET_BRIGHTNESS = 12    # target brightness when auto adjust
    ASI_HARDWARE_BIN = 13           # hardware binning of pixels
    ASI_HIGH_SPEED_MODE = 14        # high speed mode
    ASI_COOLER_POWER_PERC = 15      # cooler power percent (only cool camera)
    ASI_TARGET_TEMP = 16            # sensor's target temperature (only cool camera) don't multiply by 10
    ASI_COOLER_ON = 17              # open cooler (only cool camera)
    ASI_MONO_BIN = 18               # lead to a smaller grid at software bin mode for color camera
    ASI_FAN_ON = 19                 # only cooled camera has fan
    ASI_PATTERN_ADJUST = 20         # currently only supported by 1600 mono camera
    ASI_ANTI_DEW_HEATER = 21
    ASI_FAN_ADJUST = 22
    ASI_PWRLED_BRIGNT = 23
    ASI_USBHUB_RESET = 24
    ASI_GPS_SUPPORT = 25
    ASI_GPS_START_LINE = 26
    ASI_GPS_END_LINE = 27
    ASI_ROLLING_INTERVAL = 28

class ASI_EXPOSURE_STATUS(IntEnum):
    ASI_EXP_IDLE = 0     # idle, ready to start exposure
    ASI_EXP_WORKING = 1  # exposure in progress
    ASI_EXP_SUCCESS = 2  # exposure completed successfully, image can be read out
    ASI_EXP_FAILED = 3   # exposure failure, need to restart exposure

class ASI_CAMERA_INFO(Structure):
    _fields_ = [
        ("Name", c_char * 64),
        ("CameraID", c_int),
        ("MaxHeight", c_long),
        ("MaxWidth", c_long),
        ("IsColorCam", c_int),
        ("BayerPattern", c_int),
        ("SupportedBins", c_int * 16),
        ("SupportedVideoFormat", c_int * 8),
        ("PixelSize", c_double),
        ("MechanicalShutter", c_int),
        ("ST4Port", c_int),
        ("IsCoolerCam", c_int),
        ("IsUSB3Host", c_int),
        ("IsUSB3Camera", c_int),
        ("ElecPerADU", c_float),
        ("BitDepth", c_int),
        ("IsTriggerCam", c_int),
        ("Unused", c_char * 16)
    ]

    # prefer this over the raw .Name field
    def name(self):
        return self.Name.decode('utf-8', errors='ignore').rstrip('\x00')

    # prefer this over the .SupportedVideoFormats field
    def supported_formats(self):
        return itertools.takewhile(lambda i: i >= 0, self.SupportedVideoFormat)

    def __str__(self):
        bins = [str(b) for b in self.SupportedBins if b > 0]
        formats = {
            0: "RAW8",
            1: "RGB24",
            2: "RAW16",
            3: "Y8"
        }
        video_formats = [formats.get(f, f"Unknown({f})") for f in self.supported_formats()]

        return (
            f"Camera Name: {self.Name.decode('utf-8').rstrip(chr(0))}\n"
            f"  ID: {self.CameraID}\n"
            f"  Resolution: {self.MaxWidth} × {self.MaxHeight}\n"
            f"  Color: {'Yes' if self.IsColorCam else 'No'}\n"
            f"  Bayer Pattern: {self.BayerPattern}\n"
            f"  Supported Binning: {', '.join(bins)}\n"
            f"  Supported Video Formats: {', '.join(video_formats)}\n"
            f"  Pixel Size: {self.PixelSize:.2f} µm\n"
            f"  Mechanical Shutter: {'Yes' if self.MechanicalShutter else 'No'}\n"
            f"  ST4 Port: {'Yes' if self.ST4Port else 'No'}\n"
            f"  Cooled: {'Yes' if self.IsCoolerCam else 'No'}\n"
            f"  USB3 Host: {'Yes' if self.IsUSB3Host else 'No'}\n"
            f"  USB3 Camera: {'Yes' if self.IsUSB3Camera else 'No'}\n"
            f"  Elec/ADU: {self.ElecPerADU:.2f}\n"
            f"  Bit Depth: {self.BitDepth} bit\n"
            f"  Trigger Capable: {'Yes' if self.IsTriggerCam else 'No'}"
        )

class ASI_CONTROL_CAPS(Structure):
    _fields_ = [
        ("Name", c_char * 64),
        ("Description", c_char * 128),
        ("MaxValue", c_long),
        ("MinValue", c_long),
        ("DefaultValue", c_long),
        ("IsAutoSupported", c_int),  # ASI_BOOL
        ("IsWritable", c_int),       # ASI_BOOL
        ("ControlType", c_int),      # ASI_CONTROL_TYPE
        ("Unused", c_char * 32)
    ]

    def name(self):
        return self.Name.decode('utf-8', errors='ignore').rstrip('\x00')

class AsiCamera2:
    def __init__(self):
        arch = get_normalized_arch()
        print(f"arch = {arch}")
        self.lib = CDLL("libASICamera2.so")

        self.lib.ASIGetNumOfConnectedCameras.restype = c_int
        self.lib.ASIGetNumOfConnectedCameras.argtypes = []

        self.lib.ASIGetCameraProperty.restype = c_int
        self.lib.ASIGetCameraProperty.argtypes = [POINTER(ASI_CAMERA_INFO), c_int]

        self.lib.ASIOpenCamera.restype = c_int
        self.lib.ASIOpenCamera.argtypes = [c_int]

        self.lib.ASIInitCamera.restype = c_int
        self.lib.ASIInitCamera.argtypes = [c_int]

        self.lib.ASICloseCamera.restype = c_int
        self.lib.ASICloseCamera.argtypes = [c_int]

        self.lib.ASIGetNumOfControls.restype = c_int
        self.lib.ASIGetNumOfControls.argtypes = [c_int, POINTER(c_int)]

        self.lib.ASIGetControlCaps.restype = c_int
        self.lib.ASIGetControlCaps.argtypes = [c_int, c_int, c_void_p]  # ASI_CONTROL_CAPS*

        self.lib.ASIGetControlValue.restype = c_int
        self.lib.ASIGetControlValue.argtypes = [c_int, c_int, POINTER(c_long), POINTER(c_int)]

        self.lib.ASISetControlValue.restype = c_int
        self.lib.ASISetControlValue.argtypes = [c_int, c_int, c_long, c_int]

        self.lib.ASISetROIFormat.restype = c_int
        self.lib.ASISetROIFormat.argtypes = [c_int, c_int, c_int, c_int, c_int]

        self.lib.ASIGetStartPos.restype = c_int
        self.lib.ASIGetStartPos.argtypes = [c_int, POINTER(c_int), POINTER(c_int)]

        self.lib.ASISetStartPos.restype = c_int
        self.lib.ASISetStartPos.argtypes = [c_int, c_int, c_int]

        # self.lib.ASIPulseGuideOn.restype = c_int
        # self.lib.ASIPulseGuideOn.argtypes = [c_int, c_int]

        # self.lib.ASIPulseGuideOff.restype = c_int
        # self.lib.ASIPulseGuideOff.argtypes = [c_int, c_int]

        self.lib.ASIStartExposure.restype = c_int
        self.lib.ASIStartExposure.argtypes = [c_int, c_int]

        self.lib.ASIStopExposure.restype = c_int
        self.lib.ASIStopExposure.argtypes = [c_int]

        self.lib.ASIGetExpStatus.restype = c_int
        self.lib.ASIGetExpStatus.argtypes = [c_int, POINTER(c_int)]

        self.lib.ASIGetDataAfterExp.restype = c_int
        self.lib.ASIGetDataAfterExp.argtypes = [c_int, POINTER(c_ubyte), c_long]

        self.lib.ASISetCameraMode.restype = c_int
        self.lib.ASISetCameraMode.argtypes = [c_int, c_int]

        self.lib.ASIGetGainOffset.restype = c_int
        self.lib.ASIGetGainOffset.argtypes = [c_int, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int)]

        self.lib.ASIGetLMHGainOffset.restype = c_int
        self.lib.ASIGetLMHGainOffset.argtypes = [c_int, POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int)]

    def cameras(self):
        num_cameras = self.lib.ASIGetNumOfConnectedCameras()
        print(f"Number of connected cameras: {num_cameras}")

        cameras = []
        for i in range(num_cameras):
            cameras.append(Camera(self.lib, i))
        return cameras

# the Camera duck api
#
# read only parameters:
#
# name - string (human readable)
# bitdepth - int
# is_cooled - boolean
# has_gain - boolean
# gain - number
# gain_{min,max,default,unity} - number(s)
# offset - number
# exposure_{min,max} - number(s)
# bayer - string pattern
# pixelsize - number
# guide - boolean
#
# methods:
#
# get_temp() - current temp
# set_cooling(temp)
# set_gain(gain)
#
# capture_start(exposure) - returns immediately
# capture_wait() - True if ready, False if processing, None if failed
# capture_stop() - exit early
# capture_finish() - returns a numpy matrix image (usually 8 or 16 bit)
class Camera:

    def __init__(self, lib, i):
        self.lib = lib
        self.i = i
        self.info = ASI_CAMERA_INFO()
        self.name = None
        self.is_cooled = None
        self.has_gain = None

        self.bayer = None

        # updated to hold the last set value
        self.gain = None
        self.offset = None

        # NOTE there is a concept in indi known as "blinking" where multiple
        # very short exposures are made before changing any settings. I haven't
        # seen a need to do it.

        call(self.lib.ASIGetCameraProperty(byref(self.info), self.i))
        self.name = self.info.name()
        self.bitdepth = self.info.BitDepth
        # print(f"{self.info}")

        if self.info.IsColorCam == ASI_BOOL.ASI_TRUE:
            if self.info.BayerPattern == ASI_BAYER_PATTERN.ASI_BAYER_RG:
                self.bayer = "RGGB"
            elif self.info.BayerPattern == ASI_BAYER_PATTERN.ASI_BAYER_BG:
                self.bayer = "BGGR"
            elif self.info.BayerPattern == ASI_BAYER_PATTERN.ASI_BAYER_GR:
                self.bayer = "GRBG"
            elif self.info.BayerPattern == ASI_BAYER_PATTERN.ASI_BAYER_GB:
                self.bayer = "GBRG"

        self.pixelsize = self.info.PixelSize

        self.guide = bool(self.info.ST4Port)
        if self.name.startswith("ZWO ASI1600"):
            # older models had an ST4 port, sdk is wrong.
            self.guide = False
        if self.name.startswith("ZWO ASI585"):
            # you can technically guide with these, but, come on.
            self.guide = False

        call(self.lib.ASIOpenCamera(self.i))

        num_controls = c_int()
        call(self.lib.ASIGetNumOfControls(self.i, byref(num_controls)))

        self.controls = {}
        for c in range(num_controls.value):
            caps = ASI_CONTROL_CAPS()
            call(self.lib.ASIGetControlCaps(self.i, c, byref(caps)))
            self.controls[caps.ControlType] = caps
            # print(f"CAPS {caps.name()} = {caps.DefaultValue} ({caps.MinValue, caps.MaxValue}) auto={caps.IsAutoSupported == ASI_BOOL.ASI_TRUE}")

        self.is_cooled = ASI_CONTROL_TYPE.ASI_COOLER_ON in self.controls

        if ASI_CONTROL_TYPE.ASI_GAIN in self.controls:
            self.has_gain = True
            caps = self.controls[ASI_CONTROL_TYPE.ASI_GAIN]
            self.gain_min = caps.MinValue
            self.gain_max = caps.MaxValue
            self.gain_default = caps.DefaultValue
            self.gain_unity = get_unity_gain(self.name)

            # seems unlikely that we would have gain without offset, but play it safe
            if ASI_CONTROL_TYPE.ASI_OFFSET in self.controls:
                pOffset_HighestDR, pOffset_UnityGain, pGain_LowestRN, pOffset_LowestRN = c_int(), c_int(), c_int(), c_int()
                call(self.lib.ASIGetGainOffset(self.i, byref(pOffset_HighestDR), byref(pOffset_UnityGain), byref(pGain_LowestRN), byref(pOffset_LowestRN)))
                #print(f"ASIGetGainOffset({self.i}, {pOffset_HighestDR.value}, {pOffset_UnityGain.value}, {pGain_LowestRN.value}, {pOffset_LowestRN.value})")

                pLGain, pMGain, pHGain, pHOffset = c_int(), c_int(), c_int(), c_int()
                call(self.lib.ASIGetLMHGainOffset(self.i, byref(pLGain), byref(pMGain), byref(pHGain), byref(pHOffset)))
                #print(f"ASIGetLMHGainOffset({self.i}, {pLGain.value}, {pMGain.value}, {pHGain.value}, {pHOffset.value})")
                # could potentially set gain_{min,max} based on pLGain, pHGain

                self.gain_hdr = pLGain.value # bit of an assumption...
                self.offset_hdr = pOffset_HighestDR.value
                self.gain_lrn = pGain_LowestRN.value
                self.offset_lrn = pOffset_LowestRN.value
                self.offset_unity = pOffset_UnityGain.value
                caps = self.controls[ASI_CONTROL_TYPE.ASI_OFFSET]
                self.offset_min = caps.MinValue
                self.offset_max = caps.MaxValue

        # we assume that we can control exposure time
        exp = self.controls[ASI_CONTROL_TYPE.ASI_EXPOSURE]
        self.exposure_min = exp.MinValue / 1000000
        self.exposure_max = exp.MaxValue / 1000000

        if (self.info.IsTriggerCam == ASI_BOOL.ASI_TRUE):
            call(self.lib.ASISetCameraMode(self.i, ASI_CAMERA_MODE.ASI_MODE_NORMAL))
        call(self.lib.ASIInitCamera(self.i))

        # call(self.lib.ASISetControlValue(self.i, ASI_CONTROL_TYPE.ASI_HARDWARE_BIN, ASI_BOOL.ASI_FALSE, ASI_BOOL.ASI_FALSE))
        # call(self.lib.ASISetControlValue(self.i, ASI_CONTROL_TYPE.ASI_HIGH_SPEED_MODE, ASI_BOOL.ASI_FALSE, ASI_BOOL.ASI_FALSE))
        # call(self.lib.ASISetControlValue(self.i, ASI_CONTROL_TYPE.ASI_BANDWIDTHOVERLOAD, 40, ASI_BOOL.ASI_FALSE))

    def get_temp(self):
        if ASI_CONTROL_TYPE.ASI_TEMPERATURE not in self.controls:
            return None
        value = c_long()
        call(self.lib.ASIGetControlValue(self.i, ASI_CONTROL_TYPE.ASI_TEMPERATURE, byref(value), byref(c_int(ASI_BOOL.ASI_FALSE))))
        return value.value / 10.0

    def cooler(self):
        value = c_long()
        call(self.lib.ASIGetControlValue(self.i, ASI_CONTROL_TYPE.ASI_COOLER_POWER_PERC, byref(value), byref(c_int(ASI_BOOL.ASI_FALSE))))
        return value.value

    def set_cooling(self, temp):
        print(f"setting the target cooling of {self.name} to {temp}")
        # print("ASI_FAN_ON supported:", ASI_CONTROL_TYPE.ASI_FAN_ON in self.controls)
        if ASI_CONTROL_TYPE.ASI_FAN_ON in self.controls:
            call(self.lib.ASISetControlValue(self.i, ASI_CONTROL_TYPE.ASI_FAN_ON, ASI_BOOL.ASI_TRUE, ASI_BOOL.ASI_FALSE))
        #print("ASI_COOLER_ON supported:", ASI_CONTROL_TYPE.ASI_COOLER_ON in self.controls)
        call(self.lib.ASISetControlValue(self.i, ASI_CONTROL_TYPE.ASI_COOLER_ON, ASI_BOOL.ASI_TRUE, ASI_BOOL.ASI_FALSE))
        call(self.lib.ASISetControlValue(self.i, ASI_CONTROL_TYPE.ASI_TARGET_TEMP, temp, ASI_BOOL.ASI_FALSE))

    def set_gain(self, gain):
        v = int(gain)
        print(f"setting gain = {gain} for {self.name}")
        call(self.lib.ASISetControlValue(self.i, ASI_CONTROL_TYPE.ASI_GAIN, v, ASI_BOOL.ASI_FALSE))
        self.gain = v

        # older cameras need this, but newer ones have it auto-set in firmware
        if ASI_CONTROL_TYPE.ASI_OFFSET in self.controls:
            v = int(self.infer_offset(gain))
            print(f"setting offset = {v} (for gain = {gain}) for {self.name}")
            call(self.lib.ASISetControlValue(self.i, ASI_CONTROL_TYPE.ASI_OFFSET, v, ASI_BOOL.ASI_FALSE))
            self.offset = v

    def capture_start(self, exposure):
        #print(f"capture_start for exposure={exposure}")

        # safety
        call(self.lib.ASIStopExposure(self.i))

        # we might want to move the RoI setup elsewhere but this is safe incase
        # the user swaps between ROI video and image capture modes.
        width, height, binning, img_type = c_int(), c_int(), c_int(), c_int()
        assert self.lib.ASIGetROIFormat(self.i, byref(width), byref(height), byref(binning), byref(img_type)) == 0
        self.target_fmt = ASI_IMG_TYPE.ASI_IMG_RAW16
        if ASI_IMG_TYPE.ASI_IMG_RAW16 not in self.info.supported_formats():
            self.target_fmt = ASI_IMG_TYPE.ASI_IMG_RAW8
        if width.value != self.info.MaxWidth or height.value != self.info.MaxHeight or binning.value != 1 or img_type.value != self.target_fmt:
            print(f"resetting the RoI and bit depth ({self.target_fmt}), was ({width.value}, {height.value}, {binning.value}, {img_type.value})")
            call(self.lib.ASISetROIFormat(self.i, self.info.MaxWidth, self.info.MaxHeight, 1, self.target_fmt))

        startx, starty = c_int(), c_int()
        call(self.lib.ASIGetStartPos(self.i, byref(startx), byref(starty)))
        if startx.value != 0 or starty.value != 0:
            print(f"resetting the start pos, was ({startx.value}, {starty.value})")
            call(self.lib.ASISetStartPos(self.i, 0, 0))

        v = int(exposure * 1000000)
        call(self.lib.ASISetControlValue(self.i, ASI_CONTROL_TYPE.ASI_EXPOSURE, v, ASI_BOOL.ASI_FALSE))

        call(self.lib.ASIStartExposure(self.i, ASI_BOOL.ASI_FALSE))

    def capture_wait(self):
        status = c_int()
        call(self.lib.ASIGetExpStatus(self.i, byref(status)))

        if status.value == ASI_EXPOSURE_STATUS.ASI_EXP_WORKING:
            return False
        if status.value == ASI_EXPOSURE_STATUS.ASI_EXP_SUCCESS:
            return True

        print(f"capture error {status.value}")
        return None

    def capture_stop(self):
        call(self.lib.ASIStopExposure(self.i))

    # could allow the caller to provide the buffer which opens up the
    # possibility of reusing buffers or using mmapped files instead of RAM.
    def capture_finish(self):
        width = self.info.MaxWidth
        height = self.info.MaxHeight

        if self.target_fmt == ASI_IMG_TYPE.ASI_IMG_RAW16:
            buf_len = width * height * 2
        elif self.target_fmt == ASI_IMG_TYPE.ASI_IMG_RAW8:
            buf_len = width * height

        buf = (c_ubyte * buf_len)()
        call(self.lib.ASIGetDataAfterExp(self.i, buf, buf_len))
        call(self.lib.ASIStopExposure(self.i))

        img_array = np.ctypeslib.as_array(buf)
        if self.target_fmt == ASI_IMG_TYPE.ASI_IMG_RAW16:
            return img_array.view(np.uint16).reshape(height, width)
        elif self.target_fmt == ASI_IMG_TYPE.ASI_IMG_RAW8:
            return img_array.view(np.uint8).reshape(height, width)

    # we know the offset for highest dynamic range (~lowest gain) and lowest
    # read noise (~highest gain), and the offset for unity gain. That gives us
    # two slopes, so depending on which gain we have we can infer an appropriate
    # offset. Power users would probably want to set this manually, and we may
    # make that available through configuration or something, but this is best
    # left automated.
    def infer_offset(self, gain):
        if gain == self.gain_unity:
            return self.offset_unity
        def infer(x1, x2, y1, y2):
            m = (y2 - y1) / (x2 - x1)
            v = y1 + m * (gain - x1)
            return max(self.offset_min, min(int(v), self.offset_max))
        if not self.gain_unity:
            return infer(self.gain_hdr, self.gain_lrn, self.offset_hdr, self.offset_lrn)
        if gain < self.gain_unity:
            return infer(self.gain_hdr, self.gain_unity, self.offset_hdr, self.offset_unity)
        if gain > self.gain_unity:
            return infer(self.gain_unity, self.gain_lrn, self.offset_unity, self.offset_lrn)

class EFW_INFO(Structure):
    _fields_ = [
        ("ID", c_int),
        ("Name", c_char * 64),
        ("slotNum", c_int),
    ]

    def name(self):
        return self.Name.decode('utf-8', errors='ignore').rstrip('\x00')

    # the name always comes back as EFW for the EFWmini
    def identifier(self):
        if self.name() == "EFW" and self.slotNum == 5:
            return "ZWO EFWmini"
        return f"ZWO {self.name()} ({self.slotNum} slots)"

class EFW_ERROR_CODE(IntEnum):
    EFW_SUCCESS = 0
    EFW_ERROR_INVALID_INDEX = 1
    EFW_ERROR_INVALID_ID = 2
    EFW_ERROR_INVALID_VALUE = 3
    EFW_ERROR_REMOVED = 4
    EFW_ERROR_MOVING = 5
    EFW_ERROR_ERROR_STATE = 6
    EFW_ERROR_GENERAL_ERROR = 7
    EFW_ERROR_NOT_SUPPORTED = 8
    EFW_ERROR_INVALID_LENGTH = 9,
    EFW_ERROR_CLOSED = 10
    EFW_ERROR_END = -1

class EfwFilter:
    def __init__(self):
        arch = get_normalized_arch()
        self.dep = CDLL("libudev.so.1", mode=RTLD_GLOBAL)
        self.lib = CDLL("libEFWFilter.so")

        self.lib.EFWGetNum.restype = c_int
        self.lib.EFWGetNum.argtypes = []

        self.lib.EFWGetProperty.restype = c_int  # EFW_ERROR_CODE
        self.lib.EFWGetProperty.argtypes = [c_int, POINTER(EFW_INFO)]

        self.lib.EFWOpen.restype = c_int  # EFW_ERROR_CODE
        self.lib.EFWOpen.argtypes = [c_int]

        self.lib.EFWGetPosition.restype = c_int  # EFW_ERROR_CODE
        self.lib.EFWGetPosition.argtypes = [c_int, POINTER(c_int)]

        self.lib.EFWSetPosition.restype = c_int  # EFW_ERROR_CODE
        self.lib.EFWSetPosition.argtypes = [c_int, c_int]

        self.lib.EFWSetDirection.restype = c_int  # EFW_ERROR_CODE
        self.lib.EFWSetDirection.argtypes = [c_int, c_bool]

        # self.lib.EFWClose.restype = c_int  # EFW_ERROR_CODE
        # self.lib.EFWClose.argtypes = [c_int]

        self.lib.EFWCalibrate.restype = c_int  # EFW_ERROR_CODE
        self.lib.EFWCalibrate.argtypes = [c_int]

    # returns objects with fields: name, slots
    def wheels(self):
        num_wheels = self.lib.EFWGetNum()
        print(f"seen {num_wheels} wheels")
        wheels = []
        for i in range(num_wheels):
            wheels.append(Wheel(self.lib, i))
        return wheels

class Wheel:
    def __init__(self, lib, i):
        self.lib = lib
        self.i = i

        # EFWGetID is junk, just ignore it.

        # annoyingly, EFWGetProperty only works if we first open the EFW.
        # we assume the user doesn't disconnect it.
        #
        # set/get position cause it to move, so don't do that.
        # calibrate should be unnecessary because it happens on startup.
        #
        # set/get/calibrate are all async.
        call(self.lib.EFWOpen(self.i))
        self.info = EFW_INFO()
        call(self.lib.EFWGetProperty(self.i, byref(self.info)))
        self.name = self.info.identifier()
        self.slots = self.info.slotNum
        # sets bi-directional movement
        call(self.lib.EFWSetDirection(self.i, ASI_BOOL.ASI_FALSE))

    def calibrate(self):
        call(self.lib.EFWCalibrate(self.i))

    # -1 when in motion
    def get_slot(self):
        pos = c_int()
        call(self.lib.EFWGetPosition(self.i, byref(pos)))
        return pos.value

    # blocks if the wheel is in motion
    def set_slot(self, s):
        result = self.lib.EFWSetPosition(self.i, s)
        while result == EFW_ERROR_CODE.EFW_ERROR_MOVING:
            print("EFW waiting for wheel to stop moving")
            time.sleep(0.1)
            result = self.lib.EFWSetPosition(self.i, s)
        call(result)

    def set_slot_and_wait(self, s):
        self.set_slot(s)
        while self.get_slot() != s:
            print("EFW waiting for wheel to reach target")
            time.sleep(0.1)

def get_normalized_arch():
    raw = platform.machine().lower()
    if raw in ("x86_64", "amd64"):
        return "x64"
    elif raw in ("aarch64", "arm64"):
        return "armv8"
    else:
        return "unknown"

# super weird, ZWO give the offsets for unity gain but not the unity gain value
# so we have to figure them out from published data. collected by chatgpt from
# forums.
def get_unity_gain(camera_name):
    model_map = {
        "ASI120MM": 30, # 12 bit mode
        "ASI120MC": 30, # 12 bit mode
        "ASI220MM": 68,
        "ASI220MC": 68,
        "ASI1600MM": 139,
        "ASI1600MC": 139,
        "ASI294MM": 120,
        "ASI294MC": 120,
        "ASI183MM": 111,
        "ASI183MC": 111,
        "ASI174MM": 139,
        "ASI174MC": 139,
        "ASI533MM": 100,
        "ASI533MC": 100,
        "ASI2600MM": 100,
        "ASI2600MC": 100,
        "ASI6200MM": 100,
        "ASI6200MC": 100,
        "ASI2400MC": 100,
        "ASI071MC": 90,
        "ASI678MC": 90,
        "ASI678MM": 90,
        "ASI585MC": 180,
        "ASI462MC": 230,
        "ASI482MC": 160,
        "ASI432MM": 160,
        "ASI224MC": 252
    }
    for model, gain in model_map.items():
        if model in camera_name:
            return gain
    return None

def call(ret):
    if ret != 0:
        raise ZwoError(ret)

class ZwoError(Exception):
    def __init__(self, ret):
        super().__init__(f"return code was {ret}")

# minimal test, check we can make an exposure on a single camera
if __name__ == '__main__':
    efw = EfwFilter()
    efw.wheels()
    exit(0)

    api = AsiCamera2()
    cameras = api.cameras()
    assert len(cameras) > 0
    camera = cameras[0]

    camera.set_cooling(0)
    camera.set_gain(camera.gain_unity)
    # time.sleep(10)

    camera.capture_start(1)
    time.sleep(1)
    while True:
        status = camera.capture_wait()
        print(f"status = {status}")
        if status == False:
            time.sleep(0.1)
            continue
        else:
            break

# Local Variables:
# compile-command: "LD_LIBRARY_PATH=libasi/linux/x64 python3 zwo.py"
# End:
