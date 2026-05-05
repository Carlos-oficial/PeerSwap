"""
analyse.py — Post-simulation statistics on node states.

Usage:
    python analyse.py                     # reads node_states.json in cwd
    python analyse.py path/to/states.json

Excludes nodes that are searching for nemo (nemo=False, searches>0)
and nodes that have nemo (has_nemo=True / nemo=True) from the
"count" statistics, as they skew the distribution heavily.
"""

import json
import math
import sys
import pathlib
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────────────────────────────────────

def load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────────────────────
# Filtering
# ──────────────────────────────────────────────────────────────────────────────

def is_peerswap_special(node: dict) -> bool:
    """
    Returns True for nodes that should be excluded from count stats:
      - nodes that have nemo  (state["has_nemo"] is True)
      - nodes that are searching for nemo (state["searches"] > 0)
    """
    state = node.get("state", {})
    has_nemo   = bool(state.get("has_nemo", False))
    is_seeker  = int(state.get("searches", 0)) > 0
    return has_nemo or is_seeker


def partition(nodes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split nodes into (included, excluded) for count analysis."""
    included = [n for n in nodes if not is_peerswap_special(n)]
    excluded = [n for n in nodes if     is_peerswap_special(n)]
    return included, excluded


# ──────────────────────────────────────────────────────────────────────────────
# Statistics
# ──────────────────────────────────────────────────────────────────────────────

def extract_counts(nodes: list[dict]) -> list[float]:
    """Pull the 'count' field from each node's state (default 0)."""
    return [float(n.get("state", {}).get("count", 0)) for n in nodes]


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def median(xs: list[float]) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def std(xs: list[float], ddof: int = 1) -> float:
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    variance = sum((x - m) ** 2 for x in xs) / (len(xs) - ddof)
    return math.sqrt(variance)


def percentile(xs: list[float], p: float) -> float:
    """Linear-interpolation percentile (p in 0–100)."""
    if not xs:
        return float("nan")
    s = sorted(xs)
    idx = (p / 100) * (len(s) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] + frac * (s[hi] - s[lo])


def iqr(xs: list[float]) -> float:
    return percentile(xs, 75) - percentile(xs, 25)


def compute_stats(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    m = mean(xs)
    md = median(xs)
    sd = std(xs)
    cv = (sd / m * 100) if m != 0 else float("nan")   # coefficient of variation %
    return {
        "n":          len(xs),
        "min":        s[0],
        "max":        s[-1],
        "range":      s[-1] - s[0],
        "mean":       round(m,  4),
        "median":     round(md, 4),
        "std_dev":    round(sd, 4),
        "cv_%":       round(cv, 2),          # relative spread
        "iqr":        round(iqr(xs), 4),
        "p10":        round(percentile(xs, 10), 4),
        "p25":        round(percentile(xs, 25), 4),
        "p75":        round(percentile(xs, 75), 4),
        "p90":        round(percentile(xs, 90), 4),
        "skewness":   round(_skewness(xs, m, sd), 4),
    }


def _skewness(xs: list[float], m: float, sd: float) -> float:
    """Pearson's moment coefficient of skewness."""
    if sd == 0 or len(xs) < 3:
        return float("nan")
    n = len(xs)
    return (n / ((n - 1) * (n - 2))) * sum(((x - m) / sd) ** 3 for x in xs)


# ──────────────────────────────────────────────────────────────────────────────
# Histogram (ASCII, no dependencies)
# ──────────────────────────────────────────────────────────────────────────────

def ascii_histogram(xs: list[float], bins: int = 10, width: int = 40) -> str:
    if not xs:
        return "  (no data)"
    lo, hi = min(xs), max(xs)
    if lo == hi:
        return f"  all values = {lo}"
    step = (hi - lo) / bins
    counts = [0] * bins
    for x in xs:
        idx = min(int((x - lo) / step), bins - 1)
        counts[idx] += 1
    max_count = max(counts) or 1
    lines = []
    for i, c in enumerate(counts):
        lo_b = lo + i * step
        hi_b = lo_b + step
        bar  = "█" * int(c / max_count * width)
        lines.append(f"  {lo_b:7.1f}–{hi_b:<7.1f} │{bar:<{width}}│ {c}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# PeerSwap-specific stats
# ──────────────────────────────────────────────────────────────────────────────

def peerswap_stats(nodes: list[dict]) -> dict:
    """Aggregate search/relay/found/failed counts across all PeerSwap nodes."""
    totals: dict[str, int] = {
        "searches": 0, "found": 0, "failed": 0, "relayed": 0
    }
    for n in nodes:
        s = n.get("state", {})
        for key in totals:
            totals[key] += int(s.get(key, 0))

    success_rate = (
        totals["found"] / totals["searches"] * 100
        if totals["searches"] else float("nan")
    )
    return {**totals, "success_rate_%": round(success_rate, 1)}


# ──────────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────────

def _fmt(label: str, value: Any, unit: str = "") -> str:
    return f"  {label:<22} {value}{unit}"


def report(data: dict) -> None:
    nodes    = data.get("nodes", [])
    sim_step = data.get("step", "?")
    exported = data.get("exported_at", "?")

    print("=" * 60)
    print("  GRAPH SIMULATION — POST-RUN ANALYSIS")
    print("=" * 60)
    print(_fmt("Exported at:",    exported))
    print(_fmt("Simulation step:", sim_step))
    print(_fmt("Total nodes:",    len(nodes)))

    included, excluded = partition(nodes)
    print(_fmt("Included in stats:", len(included)))
    print(_fmt("Excluded (nemo):",   len(excluded)))

    if excluded:
        exc_labels = ", ".join(n.get("label", n["id"]) for n in excluded)
        print(f"  Excluded nodes:        {exc_labels}")

    # ── count statistics ──
    print()
    print("─" * 60)
    print("  COUNT STATISTICS  (relay/passive nodes only)")
    print("─" * 60)

    counts = extract_counts(included)
    if not counts:
        print("  No eligible nodes.")
    else:
        stats = compute_stats(counts)
        print(_fmt("N:",        stats["n"]))
        print(_fmt("Min:",      stats["min"]))
        print(_fmt("Max:",      stats["max"]))
        print(_fmt("Range:",    stats["range"]))
        print(_fmt("Mean:",     stats["mean"]))
        print(_fmt("Median:",   stats["median"]))
        print(_fmt("Std dev:",  stats["std_dev"]))
        print(_fmt("CV:",       stats["cv_%"], "%"))
        print(_fmt("IQR:",      stats["iqr"]))
        print(_fmt("P10 / P90:", f"{stats['p10']} / {stats['p90']}"))
        print(_fmt("Skewness:", stats["skewness"],
                   "  (>0 right-tailed, <0 left)"))

        print()
        print("  Distribution:")
        print(ascii_histogram(counts))

    # ── PeerSwap protocol stats ──
    ps_nodes = [n for n in nodes
                if "searches" in n.get("state", {})
                or "relayed"  in n.get("state", {})]
    if ps_nodes:
        print()
        print("─" * 60)
        print("  PEERSWAP PROTOCOL STATS")
        print("─" * 60)
        ps = peerswap_stats(ps_nodes)
        print(_fmt("Total searches:",  ps["searches"]))
        print(_fmt("Found:",           ps["found"]))
        print(_fmt("Failed (TTL exp):", ps["failed"]))
        print(_fmt("Messages relayed:", ps["relayed"]))
        print(_fmt("Success rate:",    ps["success_rate_%"], "%"))

    # ── Per-node table ──
    print()
    print("─" * 60)
    print("  PER-NODE SUMMARY")
    print("─" * 60)
    header = f"  {'Label':<18} {'Type/ID':<12} {'count':>6}  {'searches':>8}  {'found':>5}  {'nemo':>5}  excl."
    print(header)
    print("  " + "-" * 66)
    for n in nodes:
        s      = n.get("state", {})
        label  = n.get("label", "")[:17]
        nid    = n["id"]
        count  = s.get("count", "-")
        srch   = s.get("searches", "-")
        found  = s.get("found", "-")
        nemo   = "✓" if s.get("has_nemo") else ""
        excl   = "✓" if is_peerswap_special(n) else ""
        print(f"  {label:<18} {nid:<12} {str(count):>6}  {str(srch):>8}  {str(found):>5}  {nemo:>5}  {excl}")

    print()
    print("=" * 60)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "node_states.json"
    path = pathlib.Path(path)

    if not path.exists():
        print(f"[analyse] File not found: {path}")
        sys.exit(1)

    data = load(path)
    report(data)