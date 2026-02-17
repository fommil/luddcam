# initial settings
snap("settings_1")
nothing(1)
snap("settings_1e", epaper=True)

# select filter wheel
right()
snap("settings_2")

# select camera settings
right()
down()
right() # increase single / repeat exposure to 2 seconds
up()
snap("settings_3")

# select intervals
right()
snap("settings_4")

select() # close settings, go to live view
#snap("live_started")
nothing(3)
snap("live_capture")
nothing(3)
snap("live_capture_e", epaper=True)
a() # initiate zoom
nothing(3)
snap("live_zoom")
nothing(1)
snap("live_zoom_e", epaper=True)
a() # cancel zoom
start() # single 2 second exposure
#snap("capture_single_started")
nothing(4)
snap("capture_single_done")
expect_images(1)

# goes to playback
down()
snap("playback_1")
snap("playback_1_e", epaper=True)
down()
# back to capture

# still on the pause image, goes back to live
b()
nothing(2)
snap("live_again")

# selects plate solving
b()
down()
a()
snap("secondary_menu")
b()

nothing(5)
snap("live_plate")
snap("live_plate_e", epaper=True)

# enable polar alignment
a()
nothing(2)
snap("live_polar1")
snap("live_polar1_e", epaper=True)

# polar alignment without moving
a()
nothing(2)
snap("live_polar2")
snap("live_polar2_e", epaper=True)

# disable plate solving
up() # with shortcut

# selects REPEAT mode
b()
right()
b()

nothing(2)
snap("live_repeat")

start() # 2 second exposures
#snap("capture_repeat_started")
nothing(9)
start()
snap("capture_repeat_done")
# this can be bit flakey because the exposure is short
expect_images(5)

# now try the zoom feature
a()
nothing(2)
snap("capture_paused_zoom")

# create a simple schedule
select()
down(2)
a()
down()
left(3) # reduce default exposure time to 10 seconds
down()
a()
up(3)
snap("settings_4_set")
select()

# swap to intervals, 10 second exposures
right() # uses arrow hotkey

nothing(2)
snap("live_intervals")

start()
#snap("capture_intervals_started")
nothing(11)
# first capture
snap("capture_intervals_first")
b() # screen off
nothing(1)
snap("capture_intervals_screen_off")
nothing(10)
start() # pause even when screen off
snap("capture_intervals_done")

expect_images(7)

# TODO assert on the data and metadata of the output fits

# Local Variables:
# compile-command: "cd ../../ ; ./luddcam.sh test"
# End:
