**Luddcam** is a minimalist astrophotography control system. Designed for a Raspberry Pi with an LCD or ePaper screen and game controller, it brings the feel of a classic DSLR to your astrocamera.

Luddcam requires you to be physically present: focusing, framing your shot, checking your histograms, pressing the shutter, waiting patiently to see how it comes out. It's not about convenience or performance, it's about connection.

Luddcam supports a few carefully chosen "cheat codes", like plate solving, polar alignment assist, and electronic filter wheels. But they are there as helpers, not crutches. Astrophotographers are encouraged to star hop to their targets (following printed star charts) instead of using go-to, use their mount's manual tracking or periodic error correction (PEC) whenever exposure and focal lengths allow it, and to manually change filters. The luddite way is to minimise the amount of technology used for any given picture, but the main objective is to be present, under the stars.

Whether you're a DIY tinker-photographer, an analog romantic, or just someone who enjoys feeling the click of a real button under a dark sky, Luddcam is for you.

If you want to take the best picture possible, and squeeze every ounce of performance out of your gear, you don't want luddcam... instead get an [ASIAIR](https://www.zwoastro.com/product-category/asiair/), [StellaVita](https://www.touptekastro.com/en-eu/products/stellavita), [StellarMate](https://stellarmate.com/) or a laptop with [SharpCap](https://www.sharpcap.co.uk/), [NINA](https://nighttime-imaging.eu/) and [PHD2](https://openphdguiding.org/).

# User Guide

Luddcam is designed to look and feel like a DSLR and requires a gamepad controller for input:

- `START` is the shutter
- `SELECT` is the menu
- `A` is the primary action
- `B` is the secondary action
- `DOWN` changes modes (capture, playback, guiding)

When Luddcam starts up, it drops into the menu allowing selection of the camera (or filter wheel if you have one). Left is what you see with an LCD screen, right if you have e-Paper (and are saving your night vision from attack):

<p align="center">
<img src="./test_data/osc/assertions/settings_1.png" width="30%">
<img src="./test_data/osc/assertions/settings_1e.png" width="30%">
</p>

The direction buttons work as expected with up/down to select a menu entry and left/right to change it. Left/right is used to go through all the menus, e.g. to change the exposure length (with quick buttons for bias/flats/dso) and gain

<p align="center"><img src="./test_data/osc/assertions/settings_3.png" width="30%"></p>

or to label the filter wheel positions and create interval plans.

When finished with the menu, press `SELECT` to go to the capture view, which will be `LIVE` by default (capped to a few seconds maximum exposure). To return to the menu at any moment, press `SELECT`.

<p align="center">
<img src="./test_data/osc/assertions/live_capture.png" width="30%">
<img src="./test_data/osc/assertions/live_capture_e.png" width="30%">
</p>

`A` can be used to zoom in to the central region which is excellent for focusing or mount star alignment.

<p align="center">
<img src="./test_data/osc/assertions/live_zoom.png" width="30%">
<img src="./test_data/osc/assertions/live_zoom_e.png" width="30%">
</p>

By default images are rendered with an emphasis on speed (and battery saving) over quality, but we can jump to playback mode by pressing `DOWN`, which emphasises quality. We can browse all saved images with `LEFT` / `RIGHT` and zoom with `A`.

<p align="center">
<img src="./test_data/osc/assertions/playback_1.png" width="30%">
<img src="./test_data/osc/assertions/playback_1_e.png" width="30%">
</p>

Press `DOWN` to return to the capture mode. Swapping modes with `DOWN` does not
stop any ongoing captures.

To take a single shot, press `START` (the shutter). It will remain on the screen until you press `B`, going back to `LIVE` mode, `A` to zoom, or `START` to take another capture.

Some useful information is shown on screen such as your exposure, file name, gain and position in the sequence. The preview is automatically stretched with arcsinh to make it easier to frame the shot.

<p align="center"><img src="./test_data/osc/assertions/capture_repeat_done.png" width="30%"></p>

Histograms use a logarithm scale and are calculated across all the raw image pixels in their full bit depth. Also included is a count of saturated pixels (your hot pixels forever haunting you). Single shot mode is a great way to make sure you've dialled in your exposure lengths and gain.

Once you're ready to start your session, press `B` to get back into `LIVE` mode, then `B` again to get a choice of `SINGLE` / `REPEAT` / `INTERVAL` modes. Here you can also enable plate solving to help find your target if you're lost in space.

<p align="center"><img src="./test_data/osc/assertions/secondary_menu.png" width="30%"></p>

Plate solving is only enabled in `LIVE` and `SINGLE` mode to conserve power. The first plate solve is the slowest (usually taking a few seconds), but will provide hints for future solves and any subsequent `.fits` files. This introduces some lag in the display of images.

<p align="center">
<img src="./test_data/osc/assertions/live_plate.png" width="30%">
<img src="./test_data/osc/assertions/live_plate_e.png" width="30%">
</p>

When plate solving is enabled, we can check and correct our polar alignment. Press `A` to lock in the current DEC, then slew only the RA axis until the drift is at its largest (usually between 45° and 90°). Then press `A` one more time to bring up a crosshair target on screen. Using only the alt/az screws on the mount's polar wedge, center the cross hair (as in this screenshot).

<p align="center">
<img src="./test_data/osc/assertions/live_polar2.png" width="30%">
<img src="./test_data/osc/assertions/live_polar2_e.png" width="30%">
</p>

Press `A` again to finish polar alignment. You can do this as many times as you want to improve the accuracy of your alignment. It is impossible to do this in a single step without knowing the site location and time of day, so doing this 2 or 3 times is recommended.

There are some shortcuts to save visiting the `B` menu: `LEFT` / `RIGHT` iterates the interval mode and `UP` will toggle plate solving.

All files are saved as (uncompressed) fits files and are flushed to disk, so once it says `SAVED` on the screen, it's physically on the drive. A DSLR style naming convention is used so that processing follows your standard workflow and all the fits headers you'd expect to see are there.

Once you've started the session with the shutter `START` button, every image will appear on the screen as it is captured. `SELECT` (which goes to the menu) will cancel the session. `START` will pause a session.

To turn off the screen and save both your night vision and your batteries, press the `B` button. Any button will wake the screen and be otherwise ignored (except `START` or `SELECT`, which end the session).

# Hardware

Caveat: Luddcam currently only works with ZWO / Touptek cameras, ZWO filter wheels and ST4 guided mounts. If you have an EFWmini you might hit a linux kernel bug that can be resolved by following the instructions in `libasi/efw.txt`.

It is designed to run on a [Raspberry Pi 4b](https://thepihut.com/products/raspberry-pi-starter-kit?variant=20336446079038), but should run on anything more recent. I test with 4GB of RAM but it might work with 1GB / 2GB.

I recommend the [WaveShare 4.26" e-Paper](https://www.waveshare.com/wiki/4.26inch_e-Paper_HAT_Manual) (with a [Pibow Coupe](https://thepihut.com/products/pibow-4-coupe-case-for-raspberry-pi-4b) to protect from touching the electronics) or [WaveShare 4.3" LCD screen](https://thepihut.com/products/4-3-dsi-capacitive-touchscreen-display-for-raspberry-pi-800x480) ([the Amazon version includes a case](https://www.amazon.co.uk/dp/B09B29T8YF)) and [NES gamepad](https://thepihut.com/products/nes-style-raspberry-pi-compatible-usb-gamepad-controller) or [Waveshare Game HAT](https://www.amazon.co.uk/dp/B07G57BC3R). In total this should be just over $100.

After physically attaching the screen to the pi, follow these instructions to get a stock raspberry pi up and running with a microsd:

1. on PC
   1. install the latest [rpi-imager](https://www.raspberrypi.com/software/)
   1. pick the correct device
   1. choose the 64 bit operating system
   1. customise
      1. pick a hostname, e.g. `astro`
      1. `pi` as the username and password
      1. enable ssh (I use a public key)
      1. (optional) add your wifi here
   1. flash the drive and wait
1. put the sd into the raspberry pi, and turn it on
   1. `ssh pi@astro`
   2. `sudo apt update && sudo apt upgrade`
   3. turn off the desktop mode

```
sudo raspi-config

=> System Options
=> Boot / Auto Login
=> Console Text
=> Finish and reboot
```

## SPI

If you installed the screen by plugging into the HAT (40 big pins), enable SPI with:

```
sudo raspi-config
=> Interface Options => SPI => Enable
=> Finish and reboot
```

This is not a generic output device so on reboot don't expect to see the login console.

I'm able to get a little over 3 hours (on an rpi 4b and imx715 planetary camera taking 10 second exposures) with a 5amp / 120g usb power bank, and over 12 hours with a larger 20amp / 250g bank, drawing 0.9A with the screensaver on. It draws 1.0A if the screen is left running.

## DSI

If you installed the screen by plugging in an incredibly fiddly cable, enable DSI with:

Add the following entry in `/boot/firmware/config.txt` is all that is needed:

```
[all]
dtoverlay=vc4-kms-dsi-waveshare-panel,4_3_inch
```

Unfortunately the backlight cannot be turned off entirely, but we try to dim it as much as possible. I'm able to get about 3 hours (on an rpi 4b and imx715 planetary camera taking 10 second exposures) with a 5amp / 120g usb power bank, and almost 12 hours with a larger 20amp / 250g bank, drawing 1.0A with the screensaver on (i.e. dimmed). It draws 1.1A if the screen is left on.

## Power Saving

Luddcam will automatically disable cpu boost (which can cause crashes on battery packs) and try to turn off the LEDs. There's a few more things that can be done to reduce the power consumption on a raspberry pi. The ones I found that worked well are listed below. These are entirely optional:

In `/boot/firmware/config.txt`

```
# disables HDMI, only useful if you shell in remotely
dtoverlay=vc4-kms-v3d,nohdmi

# disables wifi and bluetooth, only useful if you have a wired connection
dtoverlay=disable-wifi
dtoverlay=disable-bt

# give more RAM to the CPU instead of the GPU
gpu_mem=16

# disable ethernet LEDs (not possible at runtime)
dtparam=eth_led0=4
dtparam=eth_led1=4
```

then update the eeprom (this is not done automatically with software updates)

```
sudo rpi-eeprom-update -a
```

then to reduce the power when halted

```
sudo -E rpi-eeprom-config --edit
```

and set

```
WAKE_ON_GPIO=0
POWER_OFF_ON_HALT=1
```

# Installation

This assumes that you already have a Raspberry Pi 4b (or higher) that is relatively up to date.

Download [the latest release](https://github.com/fommil/luddcam/releases) "Source code (tar.gz)" to your raspberry pi or install with `git` (recommended)

```
git clone https://github.com/fommil/luddcam.git
```

then (which will also upgrade existing installations)

```
cd luddcam
git pull
./luddcam.sh install
```

then turn it off and on again. You should see luddcam!

# Roadmap

## Version 1

### Alpha

- live focus ✅
- frame the shot ✅
- define filter intervals ✅
- store exposures in FITS ✅
- testing framework ✅
- all views have info overlays ✅

### Beta

- e-paper ✅
- plate solving ✅
- polar alignment ✅
- focus helper ✅
- playback ✅

### Gamma

- guiding

### Delta

- planetary

## Version 2

For version 2 I want to focus on hardware. There's a few directions I want to explore:

1. Cost: running on the smaller a [Raspberry Pi Zero 2](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/), possibly with a [GamePi13](https://thepihut.com/products/gamepi13-1-3-lcd-game-console-for-raspberry-pi-240-x240), could get the cost to less than $50.
2. Size: if power consumption can be optimised, and casings made small enough, it might be possible to have something that could control a RASA / HyperStar without  diffraction spikes. That could involve an [external display](https://thepihut.com/products/m5papers3-esp32s3-development-kit-with-4-7-eink-display-960-x-540).
3. Voice: a microphone and speaker could mean replacing the controller and screen, e.g. "how do I get to Casseopia?" followed by "left a bit, up a bit" responses. Or a plug-and-play standalone autoguider.

# Luddite Score

You can avoid comparing yourself with unatainable god-like images on astrobin by calculating your Luddite Score.

Start with a score of 10 and deduct a point for every electric motor that you use in a new context, e.g. align, slew, rotate, track, guide, change filters, or focus.

Many things can use two motors! And if you use the same motor for different things, you have to count it twice, e.g. star and polar alignment (2), goto slew (2), tracking (1) and guiding (2) costs 7 points in total.

An exception is made for people with gear that doesn't have physical knobs for a motor, in which case manual movements are allowed but only if the motor moves only when you are physically pressing the button.

The perfect Luddite Score is only possible with a [barn door star tracker](https://www.youtube.com/watch?v=P_qqLA0WKJg), or manually controlling the worm gears on an equatorial mount throughout the entire session! The purists say you should be using film: they can start with a score of 11. Cavemen drawing with rocks can start with 12.

Be proud to share your score in a project, no matter what it is. There's really no wrong answer so long as you enjoyed it!
