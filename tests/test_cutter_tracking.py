import unittest
from unittest.mock import ANY, patch

import numpy as np

import cutter


class TrackingFallbackStateTests(unittest.TestCase):
    def _confirmed_state(self, fps=30.0):
        state = cutter.initial_tracking_fallback_state()
        state = cutter.update_tracking_fallback_state(state, True, fps=fps)
        return cutter.update_tracking_fallback_state(state, True, fps=fps)

    def test_no_face_from_start_uses_safe_layout_not_narrow_crop(self):
        state = cutter.initial_tracking_fallback_state()
        state = cutter.update_tracking_fallback_state(state, False, fps=30.0)

        with (
            patch.object(cutter, "compose_full_frame_blur_background", return_value="safe") as safe_layout,
            patch.object(cutter, "crop_and_resize", return_value="crop") as narrow_crop,
        ):
            framed = cutter.render_frame_for_tracking_mode(object(), state["mode"], None)

        self.assertEqual(state["mode"], cutter.TRACKING_MODE_SAFE)
        self.assertEqual(framed, "safe")
        safe_layout.assert_called_once()
        narrow_crop.assert_not_called()

    def test_two_consecutive_detections_enter_tracking(self):
        state = cutter.initial_tracking_fallback_state()

        state = cutter.update_tracking_fallback_state(state, True, fps=30.0)
        self.assertEqual(state["mode"], cutter.TRACKING_MODE_SAFE)

        state = cutter.update_tracking_fallback_state(state, True, fps=30.0)
        self.assertEqual(state["mode"], cutter.TRACKING_MODE_ACTIVE)

    def test_short_face_loss_holds_last_crop(self):
        state = self._confirmed_state()
        last_crop = {"center_x": 320.0, "center_y": 180.0, "zoom": 1.0}

        state = cutter.update_tracking_fallback_state(state, False, fps=30.0)
        with (
            patch.object(cutter, "compose_full_frame_blur_background") as safe_layout,
            patch.object(cutter, "crop_and_resize", return_value="held") as narrow_crop,
        ):
            framed = cutter.render_frame_for_tracking_mode(object(), state["mode"], last_crop)

        self.assertEqual(state["mode"], cutter.TRACKING_MODE_HOLD)
        self.assertEqual(state["samples_since_confirmed"], 1)
        self.assertEqual(framed, "held")
        narrow_crop.assert_called_once_with(ANY, last_crop)
        safe_layout.assert_not_called()

    def test_long_face_loss_switches_to_safe_layout(self):
        state = self._confirmed_state()
        limit = cutter.tracking_grace_samples(30.0)

        for _ in range(limit):
            state = cutter.update_tracking_fallback_state(state, False, fps=30.0)

        self.assertEqual(state["mode"], cutter.TRACKING_MODE_SAFE)

    def test_reacquisition_requires_two_consecutive_detections(self):
        state = self._confirmed_state()
        for _ in range(cutter.tracking_grace_samples(30.0)):
            state = cutter.update_tracking_fallback_state(state, False, fps=30.0)
        self.assertEqual(state["mode"], cutter.TRACKING_MODE_SAFE)

        state = cutter.update_tracking_fallback_state(state, True, fps=30.0)
        self.assertEqual(state["mode"], cutter.TRACKING_MODE_SAFE)

        state = cutter.update_tracking_fallback_state(state, True, fps=30.0)
        self.assertEqual(state["mode"], cutter.TRACKING_MODE_ACTIVE)

    def test_isolated_detections_do_not_extend_hold_indefinitely(self):
        state = self._confirmed_state()
        detections = [False, True, False, True, False]

        for detected in detections:
            state = cutter.update_tracking_fallback_state(state, detected, fps=30.0)

        self.assertEqual(cutter.tracking_grace_samples(30.0), len(detections))
        self.assertEqual(state["mode"], cutter.TRACKING_MODE_SAFE)

    def test_grace_limit_depends_on_fps_and_analysis_stride(self):
        expected_limits = {24.0: 4, 25.0: 4, 30.0: 5, 60.0: 10}

        for fps, expected_limit in expected_limits.items():
            with self.subTest(fps=fps):
                limit = cutter.tracking_grace_samples(fps, analyze_every=5)
                elapsed = limit * 5 / fps
                self.assertEqual(limit, expected_limit)
                self.assertGreaterEqual(elapsed, cutter.FACE_LOSS_GRACE_SECONDS)
                self.assertLess(
                    elapsed,
                    cutter.FACE_LOSS_GRACE_SECONDS + (5 / fps) + 1e-9,
                )

    def test_existing_smoothing_still_averages_tracking_history(self):
        smoothed = cutter.smooth_state(
            [
                {"center_x": 100.0, "center_y": 200.0, "zoom": 1.0},
                {"center_x": 140.0, "center_y": 260.0, "zoom": 1.2},
                {"center_x": 180.0, "center_y": 320.0, "zoom": 1.4},
            ]
        )

        self.assertEqual(
            smoothed,
            {"center_x": 140.0, "center_y": 260.0, "zoom": 1.2},
        )

    def test_safe_composition_preserves_both_edges_of_landscape_frame(self):
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        frame[:, :20] = (0, 0, 255)
        frame[:, -20:] = (255, 0, 0)

        composed = cutter.compose_full_frame_blur_background(frame)

        self.assertEqual(composed.shape, (cutter.OUTPUT_HEIGHT, cutter.OUTPUT_WIDTH, 3))
        center_y = cutter.OUTPUT_HEIGHT // 2
        self.assertGreater(int(composed[center_y, 5, 2]), 200)
        self.assertGreater(int(composed[center_y, cutter.OUTPUT_WIDTH - 6, 0]), 200)


if __name__ == "__main__":
    unittest.main()
