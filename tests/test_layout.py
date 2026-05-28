import unittest

from layout import get_layout_profile, is_vertical_9_16
from strategies import get_strategy


class LayoutProfileTests(unittest.TestCase):
    def test_podcast_uses_speaker_face_crop(self):
        profile = get_layout_profile("podcast")
        self.assertEqual(profile.content_type, "podcast")
        self.assertEqual(profile.layout_mode, "speaker_face_crop")
        self.assertEqual(profile.layout_policy, "face_active_speaker")
        self.assertEqual(profile.crop_priority, "speaker_face")
        self.assertTrue(profile.allow_face_tracking)
        self.assertGreaterEqual(profile.smoothing_strength, 0.8)
        self.assertLessEqual(profile.max_crop_motion, 0.08)

    def test_legacy_content_types_resolve_to_podcast_layout(self):
        for content_type in ("gameplay", "tutorial", "commentary", "generic", None):
            with self.subTest(content_type=content_type):
                profile = get_layout_profile(content_type)
                self.assertEqual(profile.content_type, "podcast")
                self.assertEqual(profile.layout_mode, "speaker_face_crop")

    def test_layout_overrides_do_not_escape_podcast_mvp(self):
        for layout_mode in ("safe_center_crop", "gameplay_priority_crop", "full_frame_blur_background"):
            with self.subTest(layout_mode=layout_mode):
                profile = get_layout_profile("podcast", layout_mode)
                self.assertEqual(profile.content_type, "podcast")
                self.assertEqual(profile.layout_mode, "speaker_face_crop")

    def test_strategy_registry_always_selects_podcast(self):
        strategy = get_strategy("tutorial")
        payload = strategy.to_dict()

        self.assertEqual(strategy.name, "podcast")
        self.assertEqual(payload["content_type"], "podcast")
        self.assertEqual(payload["layout"]["layout_mode"], "speaker_face_crop")
        self.assertEqual(payload["render_hints"]["output_aspect_ratio"], "9:16")

    def test_is_vertical_9_16(self):
        self.assertTrue(is_vertical_9_16(1080, 1920))
        self.assertFalse(is_vertical_9_16(1920, 1080))


if __name__ == "__main__":
    unittest.main()
