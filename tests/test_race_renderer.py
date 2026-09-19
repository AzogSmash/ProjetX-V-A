import unittest

import race_renderer


class RaceRendererTests(unittest.TestCase):
    def test_track_asset_and_explicit_path_are_available(self):
        self.assertTrue(race_renderer.TRACK_ASSET.is_file())
        self.assertGreaterEqual(len(race_renderer.TRACK_PATH), 4)

    def test_official_order_is_respected_at_finish(self):
        # À la dernière frame, aucune variation visuelle ne survit : le premier
        # de l'ordre officiel est réellement le plus loin sur le circuit.
        final_order = [3, 1, 4, 0, 2]
        progresses = {
            driver: race_renderer.visual_progress(1.0, final_order.index(driver), driver)
            for driver in range(5)
        }
        visible_order = sorted(progresses, key=progresses.get, reverse=True)
        self.assertEqual(visible_order, final_order)

    def test_video_failure_is_explicit_when_ffmpeg_is_missing(self):
        original = race_renderer.shutil.which
        try:
            race_renderer.shutil.which = lambda _: None
            with self.assertRaisesRegex(RuntimeError, "FFmpeg"):
                race_renderer.render_race_video("ignored.mp4", [], [])
        finally:
            race_renderer.shutil.which = original


if __name__ == "__main__":
    unittest.main()
