# initial settings
snap("settings_1")

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

# create a simple schedule
down(2)
a()
down()
left(3) # reduce default exposure time to 10 seconds
down()
a()
up(3)
snap("settings_4_set")

select() # close settings, go to live view

# TODO make some assertions about the settings file

# use larger tolerances on the image grabs here because
# of non-determinism from which light frame
nothing(1)
snap("live_0")
nothing(3)
snap("live_1")
nothing(3)
a() # initiate zoom
nothing(3)
snap("live_zoom_1")
a() # cancel zoom
start() # single 2 second exposure
nothing(1)
snap("capture_single_started")
nothing(4)
snap("capture_single_done")
expect_images(1)

# selects REPEAT mode
left()
right()
a()
start() # 2 second exposures
nothing(1)
snap("capture_repeat_started")
nothing(8)
start()
nothing(2)
snap("capture_repeat_done")
# this can be bit flakey because the exposure is short
expect_images(5)

# swap to intervals, 10 second exposures
left()
right()
a()

start()
nothing(1)
snap("capture_intervals_started")
nothing(10)
# first capture
snap("capture_intervals_first")
# screen off
up()
nothing(1)
snap("capture_intervals_screen_off")
nothing(10)
start()
nothing(1)
snap("capture_intervals_done")

expect_images(7)

# TODO assert on the data and metadata of the output fits

# Local Variables:
# compile-command: "cd ../../../ ; make regression_tests"
# End:
