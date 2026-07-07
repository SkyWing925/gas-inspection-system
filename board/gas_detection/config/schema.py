"""Configuration schema and YAML loader."""
import yaml
import os
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass
class MOG2Config:
    history: int = 200
    var_threshold: int = 8
    detect_shadows: bool = False


@dataclass
class FrameDiffConfig:
    base_threshold: int = 8
    std_multiplier: float = 1.5


@dataclass
class OpticalFlowConfig:
    max_corners: int = 200
    quality_level: float = 0.01
    min_distance: int = 10
    lk_winsize: Tuple[int, int] = (15, 15)
    flow_grid_w: int = 64
    flow_grid_h: int = 48


@dataclass
class TemporalVarianceConfig:
    window: int = 32
    ema_alpha: float = 0.05


@dataclass
class FusionConfig:
    w_motion: float = 0.35
    w_magnitude: float = 0.25
    w_divergence: float = 0.20
    w_temporal_var: float = 0.20


@dataclass
class CVPostProcessConfig:
    min_component_area: int = 200
    max_area_ratio: float = 0.40
    binary_percentile: float = 95
    morph_open_size: int = 3
    roi_merge_dist: int = 20
    roi_persistence: int = 3


@dataclass
class CVConfig:
    enabled: bool = True
    scale: float = 0.5
    mog2: MOG2Config = field(default_factory=MOG2Config)
    frame_diff: FrameDiffConfig = field(default_factory=FrameDiffConfig)
    optical_flow: OpticalFlowConfig = field(default_factory=OpticalFlowConfig)
    temporal_variance: TemporalVarianceConfig = field(default_factory=TemporalVarianceConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    postprocess: CVPostProcessConfig = field(default_factory=CVPostProcessConfig)


@dataclass
class DLCascadeConfig:
    """Cascade gating: CV detects, DL classifies.

    CV (MOG2 + sparse OF + temporal variance) is a signal detector — it finds
    regions where *something* is happening, but cannot distinguish gas from
    humans.  DL (MobileNetV2) is the classifier that discriminates.

    Strategy (revised):
      fused_max < skip_low  → no significant signal → skip DL → "normal"
      fused_max >= skip_low → signal present → ALWAYS run DL → let DL decide

    There is no skip_high.  CV is never trusted to classify; when it fires
    strongly on a person it produces false positives that only DL can suppress.
    """
    skip_low: float = 0.15
    # t_var_confirm is retained for backward-compat but NOT used in the
    # revised gating (which runs DL on every significant signal).
    t_var_confirm: float = 0.3


@dataclass
class DLMotionConfig:
    mode: str = "bg_subtract"
    diff_threshold: int = 15
    min_motion_pixels: int = 500
    morph_kernel: int = 3


@dataclass
class DLROIConfig:
    min_area: int = 200
    max_area_ratio: float = 0.5
    persistence_frames: int = 2


@dataclass
class DLConfig:
    enabled: bool = True
    model_path: str = "gas_mobilenet_v3.pth"
    image_size: int = 224
    frame_interval: int = 5
    cascade: DLCascadeConfig = field(default_factory=DLCascadeConfig)
    motion: DLMotionConfig = field(default_factory=DLMotionConfig)
    roi: DLROIConfig = field(default_factory=DLROIConfig)


@dataclass
class TemporalConfig:
    heatmap_ema_alpha: float = 0.3
    leak_prob_ema_alpha: float = 0.4
    danger_count_threshold: int = 4
    warning_count_threshold: int = 2
    heatmap_intensity_threshold: float = 0.12


@dataclass
class CloudConfig:
    enabled: bool = False
    device_id: str = "gastest001"
    secret: str = "GasTest2026"
    server: str = "7e58fc8115.st1.iotda-device.cn-north-4.myhuaweicloud.com"
    port: int = 8883


@dataclass
class OutputConfig:
    dir: str = "output"
    save_video: bool = True
    save_images: bool = True
    save_report: bool = True
    overlay_alpha: float = 0.5
    cloud: CloudConfig = field(default_factory=CloudConfig)


@dataclass
class PipelineConfig:
    """Master configuration for the gas detection pipeline."""
    cv: CVConfig = field(default_factory=CVConfig)
    dl: DLConfig = field(default_factory=DLConfig)
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    @classmethod
    def from_yaml(cls, path: str) -> "PipelineConfig":
        """Load configuration from a YAML file."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, d: dict) -> "PipelineConfig":
        cv_data = d.get("cv", {})
        dl_data = d.get("dl", {})
        temporal_data = d.get("temporal", {})
        output_data = d.get("output", {})

        cv = CVConfig(
            enabled=cv_data.get("enabled", True),
            scale=cv_data.get("scale", 0.5),
            mog2=MOG2Config(**cv_data.get("mog2", {})),
            frame_diff=FrameDiffConfig(**cv_data.get("frame_diff", {})),
            optical_flow=OpticalFlowConfig(**cv_data.get("optical_flow", {})),
            temporal_variance=TemporalVarianceConfig(**cv_data.get("temporal_variance", {})),
            fusion=FusionConfig(**cv_data.get("fusion", {})),
            postprocess=CVPostProcessConfig(**cv_data.get("postprocess", {})),
        )

        dl = DLConfig(
            enabled=dl_data.get("enabled", True),
            model_path=dl_data.get("model_path", "gas_mobilenet_v3.pth"),
            image_size=dl_data.get("image_size", 224),
            frame_interval=dl_data.get("frame_interval", 5),
            cascade=DLCascadeConfig(**dl_data.get("cascade", {})),
            motion=DLMotionConfig(**dl_data.get("motion", {})),
            roi=DLROIConfig(**dl_data.get("roi", {})),
        )

        temporal = TemporalConfig(
            heatmap_ema_alpha=temporal_data.get("heatmap_ema_alpha", 0.3),
            leak_prob_ema_alpha=temporal_data.get("leak_prob_ema_alpha", 0.4),
            danger_count_threshold=temporal_data.get("danger_count_threshold", 4),
            warning_count_threshold=temporal_data.get("warning_count_threshold", 2),
            heatmap_intensity_threshold=temporal_data.get("heatmap_intensity_threshold", 0.12),
        )

        cloud = CloudConfig(**output_data.get("cloud", {}))
        output = OutputConfig(
            dir=output_data.get("dir", "output"),
            save_video=output_data.get("save_video", True),
            save_images=output_data.get("save_images", True),
            save_report=output_data.get("save_report", True),
            overlay_alpha=output_data.get("overlay_alpha", 0.5),
            cloud=cloud,
        )

        return cls(cv=cv, dl=dl, temporal=temporal, output=output)
