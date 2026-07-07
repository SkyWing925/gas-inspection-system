"""Gas Detection Pipeline — unified CV + DL for RDK X5.

Usage:
    from gas_detection import GasDetectionPipeline
    from gas_detection.config import PipelineConfig

    config = PipelineConfig.from_yaml("gas_detection/config/default.yaml")
    pipeline = GasDetectionPipeline(config)
    report = pipeline.run_offline("recording.npz", "output", location=1)
    print(report.to_dict())
"""

import os
import sys
import logging

from .core.pipeline import GasDetectionPipeline

__all__ = ["GasDetectionPipeline"]
