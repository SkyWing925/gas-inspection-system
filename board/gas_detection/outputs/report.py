"""Output: JSON report generator."""

import os
import json
import numpy as np
from typing import List

from ..core.result import FrameResult, DetectionReport


class ReportGenerator:
    """Build and save detection reports."""

    @staticmethod
    def build(frame_results: List[FrameResult], location: int,
              total_frames: int, fps: float) -> DetectionReport:
        """Aggregate per-frame results into a final report."""
        # Count DL results
        dl_results = [r.dl for r in frame_results if r.dl and r.dl.has_classification]
        leak_frames = [r for r in dl_results if r.class_name == "leak"]
        total_dl_frames = len(dl_results)

        # Compute leak rate
        leak_rate = len(leak_frames) / max(total_dl_frames, 1)

        # Find best leak
        best_leak = None
        max_prob = 0.0
        for r in leak_frames:
            if r.leak_prob > max_prob:
                max_prob = r.leak_prob
                best_leak = r

        # CV stats
        cv_fused_maxs = [r.cv.fused_max for r in frame_results if r.cv]
        cv_fused_means = [r.cv.fused_mean for r in frame_results if r.cv]
        cv_roi_counts = [r.cv.roi_count for r in frame_results if r.cv]

        # ================================================================
        # Decision logic (v9 — 3-condition normal gate, intensity removed)
        # ================================================================
        # NORMAL:  1) avg_flow_pts < 1.5            (no optical-flow tracking)
        #          2) total_rois < 1.2/frame        (too few ROIs overall)
        #          3) roi_frame_ratio < 15%          (too sparse)
        # DANGER:  DL confirms gas (leak_rate >= 30% AND >= 4 frames)
        # WARNING: CV saw something but DL didn't confirm
        # ================================================================

        dl_available = total_dl_frames > 0
        ROI_PER_FRAME_THRESHOLD = 1.2

        total_rois = sum(r.cv.roi_count for r in frame_results if r.cv)
        roi_threshold = int(ROI_PER_FRAME_THRESHOLD * total_frames)
        cv_mean = float(np.mean(cv_fused_means)) if cv_fused_means else 0.0

        # Active-frame ratio: fraction of frames with at least 1 ROI
        roi_active_frames = sum(1 for r in frame_results if r.cv and r.cv.roi_count > 0)
        roi_frame_ratio = roi_active_frames / max(total_frames, 1)

        # Avg optical-flow points inside ROIs (noise frames have ~0 tracked points)
        flow_pt_counts = [r.cv.flow_pt_count for r in frame_results if r.cv and r.cv.roi_count > 0]
        avg_flow_pts_in_roi = float(np.mean(flow_pt_counts)) if flow_pt_counts else 0.0

        # ---- normal 3-gate (OR logic, no intensity threshold) ----
        normal_by_noise   = avg_flow_pts_in_roi < 1.5               # 条件1: 光流不追踪=噪点
        normal_by_count   = total_rois < roi_threshold              # 条件2: ROI总数太少
        normal_by_sparse  = roi_frame_ratio < 0.15                  # 条件3: 活跃帧占比太低

        if normal_by_noise or normal_by_count or normal_by_sparse:
            result = "normal"
            reasons = []
            if normal_by_noise:
                reasons.append(f"ROI内平均光流点={avg_flow_pts_in_roi:.1f}<1.5")
            if normal_by_count:
                reasons.append(f"总ROI={total_rois}<{roi_threshold}")
            if normal_by_sparse:
                reasons.append(f"活跃帧占比={roi_frame_ratio:.0%}<15%")
            summary = f"未检测到气体泄漏 ({'; '.join(reasons)})"
        elif dl_available and leak_rate >= 0.30 and len(leak_frames) >= 4:
            # DL confirms gas → danger
            result = "danger"
            summary = (f"检测到气体泄漏! ({len(leak_frames)}/{total_dl_frames}帧, "
                      f"泄漏率={leak_rate:.0%}, 最大置信度{max_prob:.2f})")
        else:
            # CV saw meaningful ROIs but DL didn't confirm gas → warning
            if dl_available:
                summary = (f"注意: CV检测到信号 (总ROI={total_rois}, "
                          f"平均强度={cv_mean:.4f}) AI检测leak帧={len(leak_frames)}/{total_dl_frames}="
                          f"{leak_rate:.0%}")
            else:
                summary = (f"注意: CV检测到信号 (总ROI={total_rois}, "
                          f"平均强度={cv_mean:.4f}) — DL不可用，无法确认")
            result = "warning"

        report = DetectionReport(
            result=result,
            location=location,
            cv={
                "max_fused": float(max(cv_fused_maxs)) if cv_fused_maxs else 0.0,
                "mean_fused": float(np.mean(cv_fused_means)) if cv_fused_means else 0.0,
                "max_rois": int(max(cv_roi_counts)) if cv_roi_counts else 0,
            },
            dl={
                "leak_prob": float(max_prob),
                "leak_count": len(leak_frames),
                "total_classified": total_dl_frames,
                "union_bbox": list(best_leak.union_bbox) if best_leak and best_leak.union_bbox else None,
            },
            summary=summary,
        )

        return report

    @staticmethod
    def save(report: DetectionReport, output_dir: str) -> str:
        """Save report as JSON file."""
        path = os.path.join(output_dir, "result.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        return path
