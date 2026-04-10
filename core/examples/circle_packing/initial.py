# EVOLVE-BLOCK-START
"""Constructor-based circle packing for n=26 circles"""

import numpy as np


def construct_packing():
    """
    Construct a specific arrangement of 26 circles in a unit square
    that attempts to maximize the sum of their radii.

    Returns:
        Tuple of (centers, radii)
        centers: np.array of shape (26, 2) with (x, y) coordinates
        radii: np.array of shape (26,) with radius of each circle
    """
    n = 26
    centers = np.zeros((n, 2))

    # Place a circle in the center
    centers[0] = [0.5, 0.5]

    # Place 8 circles in an inner ring
    for i in range(8):
        angle = 2 * np.pi * i / 8
        centers[i + 1] = [0.5 + 0.3 * np.cos(angle), 0.5 + 0.3 * np.sin(angle)]

    # Place 16 circles in an outer ring
    for i in range(16):
        angle = 2 * np.pi * i / 16
        centers[i + 9] = [0.5 + 0.45 * np.cos(angle), 0.5 + 0.45 * np.sin(angle)]

    # Clip to unit square interior to avoid boundary violations
    centers = np.clip(centers, 0.01, 0.99)

    radii = compute_max_radii(centers)
    return centers, radii


def compute_max_radii(centers):
    """
    Compute the maximum valid radii for each circle position such that
    circles do not overlap and remain within the unit square.

    Args:
        centers: np.array of shape (n, 2)

    Returns:
        np.array of shape (n,) with non-negative radii
    """
    n = centers.shape[0]
    radii = np.ones(n)

    # Limit by distance to square borders
    for i in range(n):
        x, y = centers[i]
        radii[i] = min(x, y, 1.0 - x, 1.0 - y)

    # Iteratively resolve overlaps by proportional scaling
    for _ in range(n * n):
        changed = False
        for i in range(n):
            for j in range(i + 1, n):
                dist = float(np.linalg.norm(centers[i] - centers[j]))
                if radii[i] + radii[j] > dist:
                    scale = dist / (radii[i] + radii[j])
                    radii[i] *= scale
                    radii[j] *= scale
                    changed = True
        if not changed:
            break

    radii = np.maximum(radii, 0.0)
    return radii


# EVOLVE-BLOCK-END


# Fixed interface — not evolved
def run_experiment(**kwargs):
    """Run the circle packing algorithm and return results.

    Returns:
        Tuple of (centers, radii, sum_of_radii)
    """
    centers, radii = construct_packing()
    sum_radii = float(np.sum(radii))
    return centers, radii, sum_radii
