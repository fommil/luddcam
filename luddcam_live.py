# The live / capture mode. When this mode is selected a Thread is spawned which
# continually updates (with minimum/maximum exposures) a buffer that is
# displayed on the pygame surface.
#
# The following icons are overlaid to give some indication of status:
#
# - media icon flashes if not set
# - single vs interval icons
# - current filter and gain (reminders) numeric
# - guiding icon, green if happy, flashing red if not. Not present
#   if there is no guide camera.
#
# If there is no primary camera selected, this should show a stock image like an
# X that fills the screen. It should be blank if we are waiting on the first
# capture. If there have been 3 failures in a row, a more concerning stock image
# should be shown, e.g. a warning sign in the middle.
#
# SELECT and BACK work as normal (goes to settings menu / modal choice), but may
# be disabled or subject to a popup confirmation in some situations (see below).
#
# LEFT lets the user choose between single shot, continuous (i.e. intervals
# using the single shot settings), or intervals (defined in settings). A sets
# the value and returns to the LIVE view.
#
# A selects zoom sub-mode, which introduces a red frames indicating the region
# of interest, allowing the user to move it around on the live image before
# another A commits to the zoomed region (directional buttons continue to work
# when zoomed in). The zoom level is fixed to match the display size. Pressing A
# returns to the live view. An icon is shown to indicate that it is zoomed in.
#
# If the media is available, START takes pictures, saves to disk, then previews
# the latest image on the screen along with a histogram and some basic stats
# (e.g. count of pixels with maximum values). This happens in a loop in interval
# mode. START while a capture is in-progress should cancel a single or
# continuous shot, or pause (i.e. cancel but allowing picking up where left off)
# intervals, returning to LIVE.
#
# When viewing the last picture in single shot mode, A returns to LIVE and START
# will take another. RIGHT is reserved for future plate solving.
#
# When START has been pressed to capture, SELECT and BACK are disabled. But
# perhaps a pop-up could say "are you sure" where selection would cancel the
# capture.
#
# A design choice is that the settings are only accessible through the SELECT
# menu, we're not providing sub-menus. This keeps everything nice and simple.
#
# TODO settings needs an entry for single exposure / filter. User can't set
#      live exposure limit, we'll hardcode that (KISS).
