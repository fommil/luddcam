# Installation

Hopefully this will be one-click when I package it up as a `.deb` file.

# Dependencies

## Windows / Mac

This currently only works on a Linux desktop (or raspberry pi), ping me if you're interested.

## Linux

If you're on a desktop like Debian you may need to enable `non-free` or `contrib` to get access to ZWO libs, but these should be available on a standard raspberry pi by default:

```
sudo apt install python3-pygame python3-box devmon udevil exfat-fuse fonts-hack libasi

# workaround bug in raspberry pi devmon/exfat support
sudo ln -s mount.exfat-fuse /usr/sbin/mount.exfat
```

To have permissions to format drives, you may need to have a suitable sudoer entry in `/etc/sudoers.d/` (this is not necessary on the raspberry pi)

```
echo "${USER} ALL=(ALL) NOPASSWD: /sbin/mkfs.*" | sudo tee /etc/sudoers.d/format > /dev/null
sudo chmod 0440 /etc/sudoers.d/format
```

# LuddCam

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

# Version 2

For version 2 we're going to go custom hardware for folk with a 3d printer. I want it to look even more like a DSLR. This is a mockup of how I imagine it might look (with buttons on the top for the menu and shutter):

![v2 prototype](v2.png)
