**LuddCam** is a minimalist astrophotography control system. Designed for a Raspberry Pi with a screen and controller, it brings the feel of a classic DSLR to your astrocamera.

LuddCam requires you to be physically present: polar aligning through a scope, focusing, framing your shot, checking your histograms, pressing the shutter, waiting patiently to see how it comes out. It's not about convenience or performance, it's about connection.

LuddCam supports a few carefully chosen "cheat codes", like electronic filter wheels. But they are there as helpers, not crutches. Astrophotographers are encouraged to star hop to their targets (following printed star charts) instead of using go-to, use their mount's manual tracking or periodic error correction (PEC) whenever exposure and focal lengths allow it, and to manually change filters. The luddite way is to minimise the amount of technology used for any given picture, but the main objective is to be present, under the stars.

Whether you're a DIY tinker-photographer, an analog romantic, or just someone who enjoys feeling the click of a real button under a dark sky, LuddCam is for you.

If you want to take the best picture possible, and squeeze every ounce of performance out of your gear, get an [ASIAIR](https://www.zwoastro.com/product-category/asiair/), [StellaVita](https://www.touptekastro.com/en-eu/products/stellavita), [StellarMate](https://stellarmate.com/) or a laptop with [SharpCap](https://www.sharpcap.co.uk/), [NINA](https://nighttime-imaging.eu/) and [PHD2](https://openphdguiding.org/).

# Luddite Score

You can avoid comparing yourself with unatainable god-like images on astrobin by calculating your Luddite Score.

Start with a score of 10 and deduct a point for every electric motor that you use in a new context, e.g. align, slew, rotate, track, guide, change filters, or focus.

Many things can use two motors! And if you use the same motor for different things, you have to count it twice, e.g. star and polar alignment (2), goto slew (2), tracking (1) and guiding (2) costs 7 points in total.

An exception is made for people with gear that doesn't have physical knobs for a motor, in which case manual movements are allowed but only if the motor moves only when you are physically pressing the button.

The perfect Luddite Score is only possible with a [barn door star tracker](https://www.youtube.com/watch?v=P_qqLA0WKJg), or manually controlling the worm gears on an equatorial mount throughout the entire session! The purists say you should be using film: they can start with a score of 11. Cavemen drawing with rocks can start with 12.

Be proud to share your score in a project, no matter what it is. There's really no wrong answer so long as you enjoyed it!

# Hardware

Caveat: LuddCam currently only works with ZWO / Touptek cameras, ZWO filter wheels and ST4 guided mounts. If you have an EFWmini you might hit a linux kernel bug that can be resolved by following the instructions in `libasi/efw.txt`.

It is designed to run on a Raspberry Pi. You can use a Model 4b or anything more recent.

Beyond the [Raspberry Pi 4B with 4GB+](https://thepihut.com/products/raspberry-pi-starter-kit?variant=20336446079038), I recommend the [WaveShare 4.3" LCD screen](https://thepihut.com/products/4-3-dsi-capacitive-touchscreen-display-for-raspberry-pi-800x480) ([the Amazon version includes a case](https://www.amazon.co.uk/dp/B09B29T8YF)) and [NES gamepad](https://thepihut.com/products/nes-style-raspberry-pi-compatible-usb-gamepad-controller). In total this should be just over $100.

I've found that after physically attaching the LCD screen the following entry in `/boot/firmware/config.txt` is all that is needed:

```
[all]
dtoverlay=vc4-kms-dsi-waveshare-panel,4_3_inch
```

Another option is the [Waveshare Game HAT](https://www.amazon.co.uk/dp/B07G57BC3R) which has less screen resolution but has integrated controls, and can run off a battery. However, it's not very weather resistant, so may need a custom 3d case to lock it down a bit further.

# Installation

Download [the latest release](https://github.com/fommil/luddcam/releases) "Source code (tar.gz)" and type

```
tar xf luddcam-*.tar.gz
cd luddcam
./luddcam.sh install
```

which will need network access to install dependencies. Then, on the pi, run these commands so that it boots up into the console instead of the default graphical interface.

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
