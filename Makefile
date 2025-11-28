.PHONY: run

run:
	PYTHONUNBUFFERED=1 SDL_VIDEODRIVER=x11 SDL_AUDIODRIVER=dummy SDL_NOMOUSE=1 \
	python3 luddcam.py 2>&1 | grep -v DETECT_AVX2

regression_tests:
	PYTHONUNBUFFERED=1 SDL_VIDEODRIVER=x11 SDL_AUDIODRIVER=dummy SDL_NOMOUSE=1 \
	python3 regression_tests.py 2>&1 | grep -v DETECT_AVX2
