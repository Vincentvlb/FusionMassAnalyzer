"""Pure heatmap logic: which entities get colored and with which color.

No `adsk` import, unit-testable. The Fusion side lives in `heatmap.py`.
"""

import math
from typing import Any, List, Sequence, Tuple

from .mass_tree import KIND_BODY, KIND_OCCURRENCE, AnalysisResult, MassNode

APPEARANCE_PREFIX = "MassHeat_"
BUCKETS = 10

RGB = Tuple[int, int, int]

# light -> heavy : grey-blue, blue, yellow, orange, red
COLOR_STOPS: Sequence[Tuple[float, RGB]] = (
    (0.00, (150, 160, 175)),
    (0.25, (60, 120, 220)),
    (0.55, (250, 215, 40)),
    (0.80, (245, 140, 30)),
    (1.00, (215, 30, 30)),
)

TARGET_OCCURRENCE = "occurrence"
TARGET_BODY = "body"


def normalize(mass: float, lo: float, hi: float, log_scale: bool = False) -> float:
    """Map mass to [0, 1] between lo and hi (linear or log10)."""
    if hi <= lo:
        return 1.0 if mass > 0 else 0.0
    if log_scale:
        floor = lo if lo > 0 else hi * 1e-6
        if floor <= 0:
            return 0.0
        m = max(mass, floor)
        t = (math.log10(m) - math.log10(floor)) / (math.log10(hi) - math.log10(floor))
    else:
        t = (mass - lo) / (hi - lo)
    return min(1.0, max(0.0, t))


def bucket(t: float, buckets: int = BUCKETS) -> int:
    """Quantize t in [0,1] into 0..buckets-1 (heaviest = buckets-1)."""
    return min(buckets - 1, max(0, int(t * buckets)))


def color_at(t: float) -> RGB:
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(COLOR_STOPS, COLOR_STOPS[1:]):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(int(round(a + (b - a) * f)) for a, b in zip(c0, c1))  # type: ignore
    return COLOR_STOPS[-1][1]


def bucket_color(index: int, buckets: int = BUCKETS) -> RGB:
    """Color of a bucket (its centre)."""
    return color_at((index + 0.5) / buckets)


def appearance_name(index: int) -> str:
    return "%s%02d" % (APPEARANCE_PREFIX, index)


def heat_targets(result: AnalysisResult) -> List[Tuple[str, MassNode]]:
    """Leaf parts to color.

    - an occurrence without child occurrences -> colored as a whole (per instance)
    - bodies owned by the root or by an occurrence that also has sub-occurrences
      -> colored body by body (an occurrence override would also paint children)
    Requires a tree built with include_bodies=True for the body targets.
    """
    out: List[Tuple[str, MassNode]] = []
    for node in result.root.walk():
        if node.kind == KIND_OCCURRENCE and node.is_leaf_part:
            out.append((TARGET_OCCURRENCE, node))
        elif node.kind == KIND_BODY:
            out.append((TARGET_BODY, node))
    # drop bodies whose parent occurrence is itself a leaf target
    leaf_ids = {n.id for kind, n in out if kind == TARGET_OCCURRENCE}
    parent_of = {c.id: p for p in result.root.walk() for c in p.children}
    return [(k, n) for k, n in out
            if k == TARGET_OCCURRENCE or parent_of[n.id].id not in leaf_ids]


def assign_buckets(targets: List[Tuple[str, MassNode]], log_scale: bool = False,
                   buckets: int = BUCKETS) -> List[Tuple[str, MassNode, int]]:
    masses = [n.total_mass for _, n in targets]
    if not masses:
        return []
    positive = [m for m in masses if m > 0]
    lo = min(positive) if (log_scale and positive) else min(masses)
    hi = max(masses)
    return [(k, n, bucket(normalize(n.total_mass, lo, hi, log_scale), buckets))
            for k, n in targets]


def legend(buckets: int = BUCKETS) -> List[Any]:
    return [{"index": i, "name": appearance_name(i), "rgb": bucket_color(i, buckets)}
            for i in range(buckets)]
