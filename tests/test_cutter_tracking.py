import unittest
import tempfile
from pathlib import Path
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


class RenderEncodingPipelineTests(unittest.TestCase):
    def test_frame_encode_converts_jpeg_full_range_to_yuv420p_tv_range(self):
        with patch.object(cutter.subprocess, "run") as run:
            cutter.encode_frames_to_video(Path("frames"), Path("silent.mp4"), 25.0)

        command = run.call_args.args[0]
        self.assertEqual(
            command[command.index("-vf") + 1],
            "scale=in_range=full:out_range=tv,format=yuv420p",
        )
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")

    def test_mux_copies_preencoded_h264_and_aac_streams(self):
        with patch.object(cutter.subprocess, "run") as run:
            cutter.mux_video_with_audio(Path("silent.mp4"), Path("audio.m4a"), Path("output.mp4"))

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertEqual(command[command.index("-c:a") + 1], "copy")
        self.assertIn(["-map", "0:v:0"], [command[index : index + 2] for index in range(len(command) - 1)])
        self.assertIn(["-map", "1:a:0"], [command[index : index + 2] for index in range(len(command) - 1)])
        self.assertIn("-shortest", command)
        self.assertIn("+faststart", command)
        self.assertIn("-map_metadata", command)

    def test_face_model_download_uses_platform_curl_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            model_path = Path(temporary_directory) / "face_detector.tflite"

            def create_download(command, **_kwargs):
                destination = Path(command[command.index("--output") + 1])
                destination.write_bytes(b"model-fixture")

            with (
                patch.object(cutter, "FACE_DETECTOR_MODEL_PATH", model_path),
                patch.object(cutter.shutil, "which", side_effect=lambda name: "/usr/bin/curl" if name == "curl" else None),
                patch.object(cutter.subprocess, "run", side_effect=create_download) as run,
            ):
                resolved = cutter.ensure_face_detector_model()

            self.assertEqual(resolved, model_path)
            self.assertEqual(model_path.read_bytes(), b"model-fixture")
            self.assertFalse(model_path.with_suffix(".tflite.part").exists())
            command = run.call_args.args[0]
            self.assertEqual(command[0], "/usr/bin/curl")
            self.assertNotIn("curl.exe", command)

    def test_static_full_frame_fallback_reports_preserved_frame(self):
        hints = {
            "layout_mode": "speaker_face_crop",
            "layout_policy": "face_active_speaker",
            "crop_mode": "speaker_face_crop",
            "crop_priority": "speaker_face",
            "allow_face_tracking": True,
            "preserve_full_frame": False,
            "blur_background": False,
            "safe_center_crop": False,
            "output_width": 1080,
            "output_height": 1920,
            "output_aspect_ratio": "9:16",
        }
        with patch.object(cutter, "inspect_video_stream", return_value=(25.0, 1920, 1080)):
            stats = cutter.build_static_render_stats(
                Path("source.mp4"),
                10.0,
                2.0,
                render_hints=hints,
                tracking_mode="full_frame_blur_background",
                fallback_reason="detector unavailable",
            )

        self.assertEqual(stats["tracking_mode"], "full_frame_blur_background")
        self.assertTrue(stats["full_frame_preserved"])
        self.assertTrue(stats["blur_background"])
        self.assertEqual(stats["fallback_reason"], "detector unavailable")
        self.assertEqual(stats["encoding"]["lossy_video_encode_stages_before_subtitles"], 1)


if __name__ == "__main__":
    unittest.main()
