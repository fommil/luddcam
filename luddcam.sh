#!/bin/bash

set -euo pipefail

cd "$(dirname "$0")"

export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export SDL_NOMOUSE="${SDL_NOMOUSE:-1}"
export SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-dummy}"

if [[ -n "${DISPLAY:-}" ]]; then
    export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"
elif ls /dev/spidev* >/dev/null 2>&1; then
    export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-dummy}"
elif ls /dev/dri/card* >/dev/null 2>&1; then
    export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-kmsdrm}"
else
    export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-dummy}"
fi

MACHINE="$(uname -m)"
LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
if [ "$MACHINE" = "x86_64" ] ; then
    export LD_LIBRARY_PATH="$PWD/libtoupcam/linux/x64:$PWD/libasi/linux/x64:$LD_LIBRARY_PATH"
elif [ "$MACHINE" = "aarch64" ] ; then
    export LD_LIBRARY_PATH="$PWD/libtoupcam/linux/arm64:$PWD/libasi/linux/armv8:$LD_LIBRARY_PATH"
fi

if [ -f /proc/device-tree/model ] && grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
    IS_RPI=true
else
    IS_RPI=false
fi

#echo "luddcam.sh: LD_LIBRARY_PATH=$LD_LIBRARY_PATH"

case "${1:-}" in
    install)
        sudo apt update
        # warning: astrometry.net is massively bloated in raspberry pi and pulls in a tonne of stuff we don't need
        sudo apt install libasi python3-pygame python3-box python3-fitsio python3-sep udevil exfatprogs fonts-hack astrometry.net astrometry-data-tycho2

        if $IS_RPI ; then
            # workaround old bugs in the udevil support for exfat, works on debian
            sudo apt install exfat-fuse
            sudo ln -fs mount.exfat-fuse /usr/sbin/mount.exfat
            sudo sed -i '/default_options_exfat/s/, nonempty//' /etc/udevil/udevil.conf

            sudo mkdir /media/$USER || true
            sudo chmod 750 /media/$USER || true

            mkdir -p $HOME/.config/systemd/user || true
            sed "s~PWD~${PWD}~g" luddcam.service > $HOME/.config/systemd/user/luddcam.service

            sudo loginctl enable-linger $USER
            systemctl --user daemon-reload
            systemctl --user enable luddcam.service

            # probably installed already, used by gpio based screens
            sudo apt install python3-spidev python3-gpiozero

            # TODO should we maybe update the firmware config file
            #      to save the user from using the gui?
        fi

        # needed on debian, safe and sensible on the pi
        echo "${USER} ALL=(ALL) NOPASSWD: /sbin/mkfs.*" | sudo tee /etc/sudoers.d/format > /dev/null
        sudo chmod 0440 /etc/sudoers.d/format

        sudo install -m 444 libtoupcam/linux/udev/99-toupcam.rules /etc/udev/rules.d/
        sudo install -m 444 libasi/linux/udev/99-asi.rules /etc/udev/rules.d/
        ;;
    test)
        if [ "${2:-}" = "-force" ] ; then
            rm -f luddcam-settings.json || true
        fi

        exec python3 regression_tests.py | grep -v DETECT_AVX2
        ;;
    *)
        if $IS_RPI ; then
            # turns off the red power, and green activity, LED on rpis
            for LED in PWR ACT ; do
                echo $LED
                echo none | sudo tee /sys/class/leds/${LED}/trigger || true
                echo 0 | sudo tee /sys/class/leds/${LED}/brightness || true
            done

            # this reduces voltage spikes, be nice to the battery pack
            for cpu in /sys/devices/system/cpu/cpu[0-9]*; do
                echo $cpu
                FREQS=($(cat $cpu/cpufreq/scaling_available_frequencies))
                MID_FREQ=${FREQS[${#FREQS[@]} / 2]}
                #echo "disabling boost by setting cpu max to $MID_FREQ"
                echo $MID_FREQ | sudo tee $cpu/cpufreq/scaling_max_freq || true
            done
        fi

        exec python3 luddcam.py
        ;;
esac
