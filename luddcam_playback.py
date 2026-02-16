# modal for viewing images saved to disk

import pygame
import pygame_menu
import re

ALIGN_LEFT=pygame_menu.locals.ALIGN_LEFT

# FIXME playback, note we can use the higher quality downscales

class Menu:
    def __init__(self, directory):
        self.directory = directory
        self.current = None

    def update(self, events):

        surface = pygame.display.get_surface()
        surface.fill((255, 105, 180))

    # TODO use surface caches so we don't keep raw fits in memory
    def paint(self, f):
        pass
