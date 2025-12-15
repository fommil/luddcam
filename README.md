**LuddCam** is a minimalist astrophotography control system. Designed for a Raspberry Pi with a screen and controller, it brings the feel of a classic DSLR to your astrocamera.

LuddCam requires you to be physically present: polar aligning through a scope, focusing, framing your shot, checking your histograms, pressing the shutter, waiting patiently to see how it comes out. It's not about convenience or performance, it's about connection.

LuddCam supports a few carefully chosen "cheat codes", like electronic filter wheels. But they are there as helpers, not crutches. Astrophotographers are encouraged to star hop to their targets (following printed star charts) instead of using go-to, use their mount's manual tracking or periodic error correction (PEC) whenever exposure and focal lengths allow it, and to manually change filters. The luddite way is to minimise the amount of technology used for any given picture, but the main objective is to be present, under the stars.

Whether you're a DIY tinker-photographer, an analog romantic, or just someone who enjoys feeling the click of a real button under a dark sky, LuddCam is for you.

If you want to take the best picture possible, and squeeze every ounce of performance out of your gear, get an [ASIAIR](https://www.zwoastro.com/product-category/asiair/), [StellaVita](https://www.touptekastro.com/en-eu/products/stellavita), [StellarMate](https://stellarmate.com/) or a laptop with [SharpCap](https://www.sharpcap.co.uk/), [NINA](https://nighttime-imaging.eu/) and [PHD2](https://openphdguiding.org/).

# User Guide

LuddCam is designed to look and feel like a DSLR. The expected input is a retro NES controller and the controls are designed to be consistent between all modes:

- `START` is the shutter
- `SELECT` is the menu
- `A` is the primary action
- `B` is the secondary action

When LuddCam starts up, it drops into the menu allowing selection of the camera (or filter wheel if you have one)

<p align="center"><img src="./test_data/sony_a7iii/m31/assertions/settings_1.png" width="30%"></p>

The direction buttons work as expected with up/down to select a menu entry and left/right to change it. Left/right is used to go through all the menus, e.g. to change the exposure length (with quick buttons for bias/flats/dso) and gain

<p align="center"><img src="./test_data/sony_a7iii/m31/assertions/settings_3.png" width="30%"></p>

or to label the filter wheel positions and create interval plans.

When finished with the menu, press `SELECT` to go to the capture view, which will be `LIVE` by default (capped to a few seconds maximum exposure). To return to the menu at any moment, press `SELECT`.

<p align="center"><img src="./test_data/sony_a7iii/m31/assertions/live_capture.png" width="30%"></p>

 `A` can be used to zoom in to the central region which is excellent for focussing or mount star alignment.

<p align="center"><img src="./test_data/sony_a7iii/m31/assertions/live_zoom.png" width="30%"></p>

To take a single shot, press `START` (the shutter). It will remain on the screen until you press `B`, going back to `LIVE` mode, `A` to zoom, or `START` to take another capture.

Some useful information is shown on screen such as your exposure, file name, gain and position in the sequence. The preview is automatically stretched with arcsinh to make it easier to frame the shot.

<p align="center"><img src="./test_data/sony_a7iii/m31/assertions/capture_repeat_done.png" width="30%"></p>

Histograms use a logarithm scale and are calculated across all the raw image pixels in their full bit depth. Also included is a count of saturated pixels (your hot pixels forever haunting you). Single shot mode is a great way to make sure you've dialled in your exposure lengths and gain.

Once you're ready to start your session, press `B` to get back into `LIVE` mode, then `B` again to get a choice of `SINGLE` / `REPEAT` / `INTERVAL` modes.

All files are saved as (uncompressed) fits files and are flushed to disk, so once it says `SAVED` on the screen, even a dead battery won't ruin your night. A DSLR style naming convention is used so that processing follows your standard workflow and all the fits headers you'd expect to see are there.

Once you've started the session, every image will appear on the screen as it is captured. To turn off the screen and save both your night vision and your batteries, press the `UP` button. Any button will wake the screen again, but it's best to use `UP` just to be consistent. Note that going to the menu while a capture is in-flight will cancel it.

To pause a repeating capture session, press the shutter `START`.

`B`, `DOWN` and `RIGHT` are reserved for future use.

# Hardware

Caveat: LuddCam currently only works with ZWO / Touptek cameras, ZWO filter wheels and ST4 guided mounts. If you have an EFWmini you might hit a linux kernel bug that can be resolved by following the instructions in `libasi/efw.txt`.

It is designed to run on a Raspberry Pi. You can use a Model 4b or anything more recent.

Beyond the [Raspberry Pi 4B with 4GB+](https://thepihut.com/products/raspberry-pi-starter-kit?variant=20336446079038), I recommend the [WaveShare 4.3" LCD screen](https://thepihut.com/products/4-3-dsi-capacitive-touchscreen-display-for-raspberry-pi-800x480) ([the Amazon version includes a case](https://www.amazon.co.uk/dp/B09B29T8YF)) and [NES gamepad](https://thepihut.com/products/nes-style-raspberry-pi-compatible-usb-gamepad-controller). In total this should be just over $100.

I've found that after physically attaching the LCD screen the following entry in `/boot/firmware/config.txt` is all that is needed:

```
[all]
dtoverlay=vc4-kms-dsi-waveshare-panel,4_3_inch
```

Unfortunately the backlight cannot be turned off entirely, but we try to dim it as much as possible. I think this might be the biggest contributor to power usage; I'm able to get about 3 hours on a planetary camera taking 10 second exposures with a 5amp / 120g usb power bank, and almost 12 hours with a larger 20amp / 250g bank.

Another option is the [Waveshare Game HAT](https://www.amazon.co.uk/dp/B07G57BC3R) which has less screen resolution but has integrated controls, and can run off a battery (although it won't last very long). However, it's not very weather resistant, so may need a custom 3d case to lock it down a bit further.

# Installation

This assumes that you already have a Raspberry Pi 4b (or higher) that is relatively up to date.

Download [the latest release](https://github.com/fommil/luddcam/releases) "Source code (tar.gz)" to your raspberry pi and type

```
tar xf luddcam-*.tar.gz
cd luddcam
./luddcam.sh install
```

which will need network access to install dependencies (but is not needed thereafter). Then (again, on the pi) run these commands so that it boots up into the console instead of the default graphical interface.

```
sudo raspi-config

=> System Options
=> Boot / Auto Login
=> Console
```

If you're a developer wanting to contribute to LuddCam or to test it out on a PC, you need to check out this git repository (which requires `git lfs` to be installed for the binaries). Read the (simple) install script to find out what you need to install through your package manager. It works on Debian 13.

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

- guiding
- playback

### Gamma

- plate solving
- polar alignment

### Delta

- planetary

## Version 2

For version 2 I want to focus on hardware. There's a few directions I want to explore:

1. Night Vision: [e-paper](https://thepihut.com/products/4-26-e-paper-display-hat-800x480), much kinder on night vision than LED screens.
2. Cost: running on the smaller a [Raspberry Pi Zero 2](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/), possibly with a [GamePi13](https://thepihut.com/products/gamepi13-1-3-lcd-game-console-for-raspberry-pi-240-x240), could get the cost to less than $50.
3. Size: if power consumption can be optimised, and casings made small enough, it might be possible to have something that could control a RASA / HyperStar without  diffraction spikes. That could involve an [external display](https://thepihut.com/products/m5papers3-esp32s3-development-kit-with-4-7-eink-display-960-x-540).
4. Voice: a microphone and speaker could mean replacing the controller and screen, e.g. "how do I get to Casseopia?" followed by "left a bit, up a bit" responses. Or a plug-and-play standalone autoguider.

# Luddite Score

You can avoid comparing yourself with unatainable god-like images on astrobin by calculating your Luddite Score.

Start with a score of 10 and deduct a point for every electric motor that you use in a new context, e.g. align, slew, rotate, track, guide, change filters, or focus.

Many things can use two motors! And if you use the same motor for different things, you have to count it twice, e.g. star and polar alignment (2), goto slew (2), tracking (1) and guiding (2) costs 7 points in total.

An exception is made for people with gear that doesn't have physical knobs for a motor, in which case manual movements are allowed but only if the motor moves only when you are physically pressing the button.

The perfect Luddite Score is only possible with a [barn door star tracker](https://www.youtube.com/watch?v=P_qqLA0WKJg), or manually controlling the worm gears on an equatorial mount throughout the entire session! The purists say you should be using film: they can start with a score of 11. Cavemen drawing with rocks can start with 12.

Be proud to share your score in a project, no matter what it is. There's really no wrong answer so long as you enjoyed it!
