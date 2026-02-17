# modal for viewing images saved to disk

from pathlib import Path
import pygame
import pygame_menu

from luddcam_settings import is_back, is_left, is_right, is_up, is_down, is_start, is_action, is_button
import luddcam_settings

from luddcam_images import *

ALIGN_LEFT=pygame_menu.locals.ALIGN_LEFT

class Menu:
    def __init__(self, directory):
        self.directory = directory
        self.surface = None # the cache

        self.reset()

        self.font_large = pygame.font.Font(luddcam_settings.hack, 32)
        self.font_small = pygame.font.Font(luddcam_settings.hack, 14)

    def cancel(self):
        pass

    def reset(self):
        self.cached = False
        self.current = None
        self.zoom = False

    def update(self, events):
        surface = pygame.display.get_surface()
        width, height = surface.get_size()
        if self.surface is None:
            self.surface = pygame.Surface((width, height))

        direction = 0
        acted = False
        for event in events:
            if is_left(event):
                self.cached = False
                self.zoom = False
                direction = -1
            if is_right(event):
                self.cached = False
                self.zoom = False
                direction = 1
            if is_action(event):
                self.cached = False
                self.zoom = not self.zoom
            # TODO are we going to allow plate solving? would involve a lot of
            #      copying with the capture code

        if not self.cached and self.directory:
            files = list(Path(self.directory).glob("*.fit*"))
            count = len(files)
            if count > 0:
                idx = files.index(self.current) if self.current in files else (count - 1)
                idx += direction
                idx = idx % count
                f = str(files[idx])
                paint(self.surface, f, idx, count, self.font_small, self.zoom)
                self.cached = True

        if self.cached:
            surface.blit(self.surface, (0, 0))
        else:
            surface.fill(BLACK)
            message = "no images to playback" if self.directory else "no directory selected"
            text = self.font_large.render(message, True, WHITE)
            rect = text.get_rect(center=(width//2, height//2))
            surface.blit(text, rect)

# top level for manual testing
def paint(surface, f, idx, count, font, zoom):
    img_raw, h = load_fits(f)
    bayer = get_corrected_bayer(h)

    raw_height, raw_width = img_raw.shape
    target_width, target_height = surface.get_size()

    img_rgb, img_mono = downscale(img_raw, target_width, target_height, zoom, bayer, quality=True)
    height, width, _ = img_rgb.shape

    img_rgb = quantize(img_rgb, h.get("EXPTIME", 0) >= 1)
    img_rgb = np.transpose(img_rgb, (1, 0, 2))
    img_surface = pygame.surfarray.make_surface(img_rgb)

    offset_v = (target_height - height) // 2
    offset_h = (target_width - width) // 2
    surface.fill(BLACK)
    surface.blit(img_surface, (offset_h, offset_v))

    # a lot of this overlaps with luddcam_capture but it's subtly different
    # enough to warrant copy pasta.
    if (bitdepth := h.get("BITDEPTH", 8 * img_raw.dtype.itemsize)):
        hist_width = 128
        hist, saturated = histogram(img_raw, hist_width, bitdepth)
        render_histogram(surface, hist, saturated, font, 10, 10)

    def append_h(s, key, prefix = "", suffix = "", align = True):
        return tab_append_lookup(h, s, key, prefix, suffix, align)

    top_left = ""

    top_left = append_h(top_left, "EXPTIME", suffix = "s")
    top_left = append_h(top_left, "GAIN", prefix=" ", suffix = "cB", align=False) # centibel = 0.1dB
    top_left = append_h(top_left, "FILTER", prefix=" ", align=False)
    top_left = append_h(top_left, "FOCALLEN", prefix=" ", suffix="mm", align=False)

    top_left_text = font.render(top_left, True, WHITE)
    top_left_rect = top_left_text.get_rect()
    top_left_rect.topleft = (10, 10)
    surface.blit(top_left_text, top_left_rect)

    top_right = ""
    top_right = tab(top_right) + f"{idx + 1}/{count}"
    top_right = tab(top_right) + Path(f).name.split(".")[0]
    top_right = tab(top_right) + "PLAYBACK"

    text = font.render(top_right, True, WHITE)
    rect = text.get_rect()
    rect.topright = (target_width - 10, 10)
    surface.blit(text, rect)

if __name__ == "__main__":
    pygame.font.init()
    font = pygame.font.Font(luddcam_settings.hack, 14)

    # f = "test_data/osc/exposures/10.fit.fz"
    # f = "test_data/osc/exposures/1.fit.fz"
    f = "tmp/IMG_00020.fit"

    zoom = True

    surface = pygame.Surface((800, 600))
    paint(surface, f, 10, 100, font, zoom)

    pygame.image.save(surface, "test.png")
    subprocess.Popen(["feh", "--force-aliasing", "test.png"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Local Variables:
# compile-command: "python3 luddcam_playback.py"
# End:
