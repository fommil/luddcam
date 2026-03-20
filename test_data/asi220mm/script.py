# This uses data captured from an asi220mm on a 150mm guide scope
# on 2026-03-08.
#
# The primary purpose of the session was to gather short exposures for guiding
# testing but I also did a few shots at the beginning of the session for
# alignment testing. Alignment is off by just under a degree and was only
# done using the polar scope of the iOptron sky tracker pro.

# change exposure to 4 seconds (just nice to match reality)
right()
right()
down()
right()
right()
right()
snap("settings_3")

# go to live view
select()
b()
down()
down()
a() # enable ngc catalog
down()
# change live cap to 4 seconds
right()
right()
right()
snap("secondary_action_1")
b()

nothing(4)
snap("live_1")

up()
nothing(10)
snap("live_1_plate")

# engage alignment
a()
nothing(4)
snap("live_1_align")

# big waits for plate solving (pure computer, not warped)
step()
nothing(10)
snap("live_2_align")

step()
nothing(10)
snap("live_3_align")

a()
nothing(4)
snap("live_3_align_result")

# some plate solves for fun
a()
nothing(4)
snap("live_3_plate")
step()
step()
nothing(10)
snap("live_2_plate")


# Local Variables:
# compile-command: "cd ../../ ; ./luddcam.sh test asi220mm"
# End:
