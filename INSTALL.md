# Installation

## Dependencies

We require some python dependency management tools

```
sudo apt install python3-pygame python3-box

# not used yet, but I intend to use them
sudo apt install python3-opencv python3-photutils python3-astropy
```

To be able to write to USB drives, we require `udevil` and `devmon` to be installed on the rpi. These should be installed by default but just to be sure, make sure to type:

```
sudo apt install udevil exfat-fuse
sudo ln -s mount.exfat-fuse /usr/sbin/mount.exfat
```

and to look pretty

```
sudo apt install fonts-hack
```

## User Service

We'll define it as a user service and turn off the desktop.

```
sudo raspi-config

=> System Options
=> Boot / Auto Login
=> Console
```

Then check out this repo and install the required services:

```
git clone git@github.com:fommil/luddcam.git
cd luddcam

# this installs things as the user, not system-wide
python3 -m pip install --user --break-system-packages pygame_menu

mkdir -p ~/.config/systemd/user
ln -s $PWD/luddcam.service ~/.config/systemd/user/

sudo loginctl enable-linger $(whoami)
systemctl --user daemon-reload
systemctl --user enable luddcam.service
```

Then when you reboot, it should start up!

# Developers

To run on your local dev machine, prefer to use your system installed version of pygame and opencv. Then use the above command to install pygame_menu. This sucks, I'd rather have a reproducible build environment, but it turns out that pipenv doesn't really work on the pi. You should be able to use pyenv to isolate everything if you really want to but you'll still be pulling in mystery meat binaries. Let's just hope there are no major mismatches in versions: I've at least tried to stick to only things that are available on the rpi.

To get access to ZWO usb devices you may need to add the following udev rules

```
sudo tee /etc/udev/rules.d/99-zwo.rules > /dev/null <<'EOF'
# ZWO USB device access
SUBSYSTEM=="usb", ATTR{idVendor}=="03c3", GROUP="plugdev", MODE="0660", TAG+="uaccess"

# ZWO EFW HID device access
KERNEL=="hidraw*", ATTRS{idVendor}=="03c3", GROUP="plugdev", MODE="0660", TAG+="uaccess"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```

and to have permissions to format drives, you may need to have a suitable sudoer entry in `/etc/sudoers.d/`

```
echo "${USER} ALL=(ALL) NOPASSWD: /sbin/mkfs.*" | sudo tee /etc/sudoers.d/format > /dev/null
sudo chmod 0440 /etc/sudoers.d/format
```

# Version 2

For version 2 we're going to go custom hardware for folk with a 3d printer. I want it to look even more like a DSLR. This is a mockup of how I imagine it might look (with buttons on the top for the menu and shutter):

![v2 prototype](v2.png)
