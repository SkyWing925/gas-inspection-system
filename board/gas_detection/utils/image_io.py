"""Utils package."""

import os
import numpy as np


def build_background(images, n_iter=3, sigma=2.0):
    """Build background via iterative median filtering (from video3).

    Args:
        images: list of (name, gray_array) tuples
        n_iter: number of refinement iterations
        sigma: outlier threshold in standard deviations

    Returns:
        uint8 background image
    """
    stack = np.stack([g for _, g in images], axis=0).astype(np.float32)
    N = stack.shape[0]
    bg = np.median(stack, axis=0)
    valid = np.ones(N, dtype=bool)

    for _ in range(n_iter):
        diffs = np.abs(stack - bg).mean(axis=(1, 2))
        threshold = diffs[valid].mean() + sigma * diffs[valid].std()
        new_valid = diffs < threshold
        if new_valid.sum() == valid.sum():
            break
        valid = new_valid
        if valid.sum() >= 3:
            bg = np.median(stack[valid], axis=0)

    return np.clip(np.round(bg), 0, 255).astype(np.uint8)
