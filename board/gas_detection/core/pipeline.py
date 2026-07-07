"""Unified gas detection pipeline orchestrator."""

import os
import time
import logging
import cv2
import numpy as np
from typing import Optional

from ..config.schema import PipelineConfig
from .frame_source import FrameSource, NpzFileSource, VideoFileSource
from .result import CVFrameResult, DLFrameResult, FrameResult, DetectionReport
from ..cv_pipeline.motion import MotionDetector
from ..cv_pipeline.optical_flow import SparseOpticalFlow
from ..cv_pipeline.temporal_variance import TemporalVariance
from ..cv_pipeline.postprocess import fuse_channels, PostProcessor
from ..dl_pipeline.motion_detector import MotionDetector as DLMotionDetector
from ..dl_pipeline.roi_filter import GasRegionFilter
from ..dl_pipeline.classifier import GasClassifier
from ..outputs.visualizer import Visualizer
from ..outputs.video_writer import VideoMultiplexer
from ..outputs.report import ReportGenerator
from ..outputs.cloud_uploader import CloudUploader

logger = logging.getLogger("gas_detection.pipeline")


class GasDetectionPipeline:
    """Unified gas detection pipeline for RDK X5.

    Combines classical CV (MOG2 + sparse OF + temporal variance -> heatmap)
    with deep learning (MobileNetV2 cascade -> classification).
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.cv_config = config.cv
        self.dl_config = config.dl
        self.temporal_config = config.temporal
        self.output_config = config.output

        self.motion_detector: Optional[MotionDetector] = None
        self.optical_flow: Optional[SparseOpticalFlow] = None
        self.temporal_variance: Optional[TemporalVariance] = None
        self.post_processor: Optional[PostProcessor] = None

        self.dl_motion: Optional[DLMotionDetector] = None
        self.roi_filter: Optional[GasRegionFilter] = None
        self.classifier: Optional[GasClassifier] = None

        self.visualizer = Visualizer()
        self.cloud_uploader: Optional[CloudUploader] = None

        self._init_components()

    def _init_components(self) -> None:
        if self.cv_config.enabled:
            self.motion_detector = MotionDetector(self.cv_config.mog2, self.cv_config.frame_diff)
            self.optical_flow = SparseOpticalFlow(self.cv_config.optical_flow, self.cv_config.scale)
            self.temporal_variance = TemporalVariance(self.cv_config.temporal_variance)
            self.post_processor = PostProcessor(self.cv_config.postprocess, self.temporal_config)

        if self.dl_config.enabled:
            self.dl_motion = DLMotionDetector(self.dl_config.motion)
            self.roi_filter = GasRegionFilter(self.dl_config.roi)
            self.classifier = GasClassifier(self.dl_config)

        if self.output_config.cloud.enabled:
            c = self.output_config.cloud
            self.cloud_uploader = CloudUploader(c.device_id, c.secret, c.server, c.port)

    def run_offline(self, input_path: str, output_dir: str,
                    location: int = 1,
                    npz_start: float = 0.0, npz_step: float = 0.0,
                    npz_count: int | None = None, npz_fps: float | None = None
                    ) -> DetectionReport:
        """Process npz/avi file with 2-pass approach."""
        if input_path.endswith(".npz"):
            source: FrameSource = NpzFileSource(
                input_path,
                fps=npz_fps or 25.0,
                start_sec=npz_start,
                step_sec=npz_step,
                max_count=npz_count,
            )
        else:
            source = VideoFileSource(input_path)

        source.open()
        fps = source.fps
        total_frames = source.frame_count or 0
        w, h = source.width, source.height
        logger.info(f"Offline: {input_path} {w}x{h}, {total_frames}f, {fps}fps")

        os.makedirs(output_dir, exist_ok=True)
        cv_scale = self.cv_config.scale
        cw, ch = int(w * cv_scale), int(h * cv_scale)

        # Pass 1: Read + CV
        logger.info("Pass 1: CV features...")
        gray_frames = []
        cv_results: list[Optional[CVFrameResult]] = []
        frame_idx = 0
        prev_gray_cv: Optional[np.ndarray] = None
        prev_heatmap_clean: Optional[np.ndarray] = None

        while True:
            gray = source.read()
            if gray is None:
                break
            gray_frames.append(gray)
            gray_cv = cv2.resize(gray, (cw, ch)) if cv_scale != 1.0 else gray

            if self.motion_detector and self.optical_flow and self.temporal_variance and self.post_processor:
                motion_map = self.motion_detector.process(gray_cv)
                if prev_gray_cv is not None:
                    flow_mag, flow_div = self.optical_flow.process(gray_cv)
                else:
                    flow_mag = np.zeros((ch, cw), dtype=np.float32)
                    flow_div = np.zeros((ch, cw), dtype=np.float32)
                t_var = self.temporal_variance.process(gray_cv)
                fused = fuse_channels(motion_map, flow_mag, flow_div, t_var, self.cv_config.fusion)
                fused_clean = self.post_processor.clean_heatmap(fused, prev_heatmap_clean, self.temporal_config.heatmap_ema_alpha)
                prev_heatmap_clean = fused_clean
                binary = self.post_processor.binary_mask(fused)
                cv_rois = self.post_processor.extract_rois(fused, ch, cw)

                # Count optical flow tracked points inside ROIs
                flow_pt_count = 0
                if cv_rois and self.optical_flow and self.optical_flow.tracked_positions is not None:
                    pts = self.optical_flow.tracked_positions  # (N, 2) at CV scale
                    for (rx, ry, rw, rh) in cv_rois:
                        in_roi = (pts[:, 0] >= rx) & (pts[:, 0] < rx + rw) & \
                                 (pts[:, 1] >= ry) & (pts[:, 1] < ry + rh)
                        flow_pt_count += int(in_roi.sum())

                cv_result = CVFrameResult(frame_idx=frame_idx, fused_map=fused_clean,
                                          binary_mask=binary, fused_max=float(fused.max()),
                                          fused_mean=float(fused.mean()), roi_count=len(cv_rois),
                                          roi_boxes=cv_rois, flow_pt_count=flow_pt_count)
            else:
                cv_result = None
            cv_results.append(cv_result)
            prev_gray_cv = gray_cv
            frame_idx += 1
            if frame_idx % 50 == 0:
                logger.info(f"  Frame {frame_idx}/{total_frames}")

        source.close()
        actual_frames = frame_idx
        logger.info(f"  Read {actual_frames} frames")

        # Pass 2: DL + output
        logger.info("Pass 2: DL + output...")

        # Gate: only run DL if CV saw enough ROIs (avg 1.2/frame)
        ROI_PER_FRAME_THRESHOLD = 1.2
        dl_roi_threshold = int(ROI_PER_FRAME_THRESHOLD * actual_frames)
        total_cv_rois = sum(r.roi_count for r in cv_results if r)
        if total_cv_rois < dl_roi_threshold:
            logger.info(f"Total CV ROIs ({total_cv_rois}) < {dl_roi_threshold} (avg {ROI_PER_FRAME_THRESHOLD}/frame), skipping DL stage")
            if self.dl_config.enabled:
                self.dl_config.enabled = False

        video_writer: Optional[VideoMultiplexer] = None
        if self.output_config.save_video:
            video_writer = VideoMultiplexer(output_dir, fps, (w, h))

        frame_results: list[FrameResult] = []

        if self.classifier and self.dl_config.enabled:
            model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), self.dl_config.model_path)
            if os.path.exists(model_path):
                try:
                    self.classifier.load_model(model_path)
                    logger.info(f"DL model loaded")
                except Exception as e:
                    logger.warning(f"DL model failed: {e}")
                    self.dl_config.enabled = False
            else:
                logger.warning(f"DL model not found, disabling")
                self.dl_config.enabled = False

        leak_prob_ema = 0.0
        danger_count = 0
        best_leak_frame: Optional[np.ndarray] = None
        best_leak_prob = 0.0
        best_leak_union: Optional[tuple] = None
        any_motion_frame: Optional[np.ndarray] = None  # fallback for "normal"

        # Reuse gray_frames from Pass 1 instead of re-reading (ensures frame alignment)
        for i in range(actual_frames):
            gray = gray_frames[i]
            cv_result = cv_results[i] if i < len(cv_results) else None

            # DL branch
            dl_result: Optional[DLFrameResult] = None
            if self.dl_motion and self.roi_filter and self.classifier and self.dl_config.enabled:
                fused_max = cv_result.fused_max if cv_result else 0.0
                roi_count = cv_result.roi_count if cv_result else 0
                roi_boxes_cv = cv_result.roi_boxes if cv_result else []
                roi_count_total = len(roi_boxes_cv)
                # Scale ROI boxes for DL (original resolution)
                if cv_scale != 1.0:
                    sx, sy = w/cw, h/ch
                    roi_boxes_orig = [(int(x*sx), int(y*sy), int(bw*sx), int(bh*sy)) for (x,y,bw,bh) in roi_boxes_cv]
                else:
                    roi_boxes_orig = roi_boxes_cv

                should_cls = self.classifier.should_classify(
                    fused_max, roi_count_total, i, gray, roi_boxes_orig,
                )

                if should_cls:
                    cls_result, union_box = self.classifier.classify(gray, roi_boxes_orig)
                    dl_result = DLFrameResult(frame_idx=i, has_classification=True,
                                              class_name=cls_result["class"],
                                              leak_prob=cls_result["leak_prob"],
                                              normal_prob=cls_result["normal_prob"],
                                              union_bbox=union_box)
                    alpha_lp = self.temporal_config.leak_prob_ema_alpha
                    leak_prob_ema = alpha_lp * cls_result["leak_prob"] + (1-alpha_lp) * leak_prob_ema
                    if leak_prob_ema > 0.5:
                        danger_count += 1
                    elif leak_prob_ema < 0.3:
                        danger_count = max(0, danger_count - 1)
                    if cls_result["leak_prob"] > best_leak_prob:
                        best_leak_prob = cls_result["leak_prob"]
                        best_leak_union = union_box
                        best_leak_frame = gray.copy()
                elif roi_count_total == 0:
                    dl_result = DLFrameResult(frame_idx=i, has_classification=False)
                    danger_count = max(0, danger_count - 1)
                else:
                    # Signal present but DL skipped (not sampled frame)
                    dl_result = DLFrameResult(frame_idx=i, has_classification=False)

            frame_results.append(FrameResult(cv=cv_result, dl=dl_result))

            # Track fallback frame for "normal" result
            if any_motion_frame is None and cv_result is not None and cv_result.fused_max > 0.01:
                any_motion_frame = gray.copy()

            # Video output
            if video_writer and cv_result is not None:
                if cv_scale != 1.0:
                    fused_full = cv2.resize(cv_result.fused_map, (w, h), interpolation=cv2.INTER_LINEAR)
                    binary_full = cv2.resize(cv_result.binary_mask, (w, h), interpolation=cv2.INTER_NEAREST)
                else:
                    fused_full = cv_result.fused_map
                    binary_full = cv_result.binary_mask
                heatmap = self.visualizer.heatmap_to_bgr(fused_full)
                overlay = self.visualizer.blend_overlay(gray, heatmap, self.output_config.overlay_alpha)
                if dl_result and dl_result.has_classification:
                    if cv_scale != 1.0 and cv_result and cv_result.roi_boxes:
                        sx, sy = w/cw, h/ch
                        roi_boxes_orig = [(int(x*sx), int(y*sy), int(bw*sx), int(bh*sy)) for (x,y,bw,bh) in cv_result.roi_boxes]
                    elif cv_result:
                        roi_boxes_orig = cv_result.roi_boxes
                    else:
                        roi_boxes_orig = []
                    cls_info = {"class": dl_result.class_name, "leak_prob": dl_result.leak_prob, "normal_prob": dl_result.normal_prob}
                    overlay = self.visualizer.draw_rois(overlay, roi_boxes_orig, dl_result.union_bbox, cls_info)
                if danger_count >= self.temporal_config.danger_count_threshold:
                    status = "danger"
                elif danger_count >= self.temporal_config.warning_count_threshold:
                    status = "warning"
                else:
                    status = "normal"
                overlay = self.visualizer.draw_status(overlay, status)
                video_writer.write(binary_full, heatmap, overlay)

            if (i+1) % 50 == 0:
                logger.info(f"  Writing {i+1}/{actual_frames}")

        if video_writer:
            video_writer.close()

        # Report
        report = ReportGenerator.build(frame_results, location, actual_frames, fps)
        # ReportGenerator.build() already implements the new ternary logic:
        #   entity_detected → "warning"  |  enough_leak → "danger"  |  else → "normal"
        # so we trust its result (no more danger_count override)

        if self.output_config.save_report:
            ReportGenerator.save(report, output_dir)
            logger.info(f"Report: {report.result} — {report.summary}")

        # ---- Save ONE representative image that best shows the result ----
        if self.output_config.save_images:
            if report.result == "danger" and best_leak_frame is not None:
                # Danger: frame with highest leak probability + bounding box
                result_img = self.visualizer.draw_leak_max(best_leak_frame,
                    best_leak_union or (0,0,0,0), best_leak_prob)
            elif report.result == "warning" and best_leak_frame is not None:
                # Warning: use best leak frame
                result_img = self.visualizer.draw_leak_max(best_leak_frame,
                    best_leak_union or (0,0,0,0), best_leak_prob)
            elif any_motion_frame is not None:
                # Normal: any frame with some activity
                result_img = cv2.cvtColor(any_motion_frame, cv2.COLOR_GRAY2BGR)
                cv2.putText(result_img, "NORMAL", (10, 25),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            elif best_leak_frame is not None:
                result_img = cv2.cvtColor(best_leak_frame, cv2.COLOR_GRAY2BGR)
                cv2.putText(result_img, "NORMAL", (10, 25),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            elif len(gray_frames) > 0:
                # Ultimate fallback: first frame, labeled
                result_img = cv2.cvtColor(gray_frames[0], cv2.COLOR_GRAY2BGR)
                cv2.putText(result_img, "NORMAL (no activity)", (10, 25),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            else:
                result_img = None

            if result_img is not None:
                cv2.imwrite(os.path.join(output_dir, "result.png"), result_img)
                logger.info(f"Representative image saved: result.png ({report.result})")

            # Also save leak_max.png if a leak frame exists (for backwards compat)
            if best_leak_frame is not None:
                leak_img = self.visualizer.draw_leak_max(best_leak_frame,
                    best_leak_union or (0,0,0,0), best_leak_prob)
                cv2.imwrite(os.path.join(output_dir, "leak_max.png"), leak_img)

        if self.cloud_uploader:
            self.cloud_uploader.send_alert(report.result, location, report.summary)
            lp = os.path.join(output_dir, "leak_max.png")
            if os.path.exists(lp):
                self.cloud_uploader.send_image(lp, report.result, location, report.summary)

        logger.info(f"Done: {report.result} loc={location}")
        return report

    def run_realtime(self, duration_sec=None, output_dir="output", location=1):
        raise NotImplementedError("Realtime mode coming soon")
