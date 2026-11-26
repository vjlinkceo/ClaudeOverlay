import pathlib
import tempfile
import unittest

from PIL import Image

from overlay.assets import bobo


class SpriteTests(unittest.TestCase):
    def test_every_frame_is_a_full_grid_of_known_pixels(self):
        for state, frames in bobo.STATES.items():
            self.assertTrue(frames, state)
            for rows, ms in frames:
                self.assertEqual(len(rows), bobo.SIZE, state)
                self.assertTrue(all(len(row) == bobo.SIZE for row in rows), state)
                self.assertTrue(set("".join(rows)) <= set(bobo.PALETTE), state)
                self.assertGreater(ms, 0, state)

    def test_states_differ(self):
        first = {state: frames[0][0] for state, frames in bobo.STATES.items()}
        self.assertEqual(len({tuple(rows) for rows in first.values()}), len(first))

    def test_unknown_state_falls_back_to_sleep(self):
        self.assertIs(bobo.frames("dancing"), bobo.STATES["sleep"])

    def test_icon_faces(self):
        for state in bobo.ICON_FOR_STATE:
            rows = bobo.icon_rows(state)
            self.assertEqual((len(rows), len(rows[0])), (bobo.ICON_SIZE, bobo.ICON_SIZE), state)
            self.assertTrue(set("".join(rows)) <= set(bobo.PALETTE), state)
        self.assertNotEqual(bobo.icon_rows("sleep"), bobo.icon_rows("listening"))

    def test_patch_keeps_pixels_under_dots(self):
        rows = bobo.apply(["FFF", "FFF"], (0, 1, [".K", "K."]))
        self.assertEqual(rows, ["FFK", "FKF"])
        self.assertEqual(bobo.shift(["AB.", "CD."], 1), [".AB", ".CD"])

    def test_render_scales_without_smoothing(self):
        image = bobo.render(bobo.bust(), 3)
        self.assertEqual(image.size, (bobo.SIZE * 3, bobo.SIZE * 3))
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        colors = {image.getpixel((x, y)) for x in range(image.width) for y in range(image.height)}
        self.assertTrue(colors <= set(bobo.PALETTE.values()) | {(0, 0, 0, 0)})

    def test_ico_has_four_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bobo.ico"
            bobo.build_ico(path)
            with Image.open(path) as ico:
                self.assertEqual(sorted(ico.info["sizes"]), [(16, 16), (32, 32), (48, 48), (256, 256)])


if __name__ == "__main__":
    unittest.main()
