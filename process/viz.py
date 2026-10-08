"""Rendering: DOT for anything real, hand-written SVG for small pictures.

The split is deliberate. A 42-activity unfiltered DFG laid out by hand looks
bad and costs days, so DOT (consumed by graphviz, which stays an optional
system tool) is the primary artifact. Hand-written SVG is reserved for the
cases where it pays: a filtered DFG of a dozen nodes, a process tree, and the
charts -- so the hosted explorer needs no system dependency and no AGPL
library.

Nothing in this module is imported by the mining core.
"""

from __future__ import annotations

import html
from pathlib import Path

from process.dfg import DFG, EdgeStats


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _penwidth(count: int, maximum: int) -> float:
    if maximum <= 0:
        return 1.0
    return 1.0 + 5.0 * (count / maximum) ** 0.5


def _heat(count: int, maximum: int) -> str:
    """Light-to-dark blue by relative frequency. Readable in both themes."""
    if maximum <= 0:
        return "#dbeafe"
    shade = (count / maximum) ** 0.5
    palette = ["#eff6ff", "#dbeafe", "#bfdbfe", "#93c5fd", "#60a5fa", "#3b82f6"]
    return palette[min(int(shade * len(palette)), len(palette) - 1)]


def dfg_to_dot(
    graph: DFG,
    *,
    annotate: str = "frequency",
    title: str | None = None,
) -> str:
    """Render a DFG as DOT. `annotate` is "frequency" or "performance"."""
    if annotate not in ("frequency", "performance"):
        raise ValueError("annotate must be 'frequency' or 'performance'")

    max_edge = max((stats.count for stats in graph.edges.values()), default=0)
    max_activity = max(graph.activity_counts.values(), default=0)
    label = title or f"{graph.log.log_id} ({annotate})"

    lines = [
        "digraph process {",
        '  rankdir="TB";',
        f'  label="{html.escape(label)}";',
        '  labelloc="t";',
        '  fontname="Helvetica";',
        '  node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10];',
        '  edge [fontname="Helvetica" fontsize=9];',
        '  __start [shape=circle label="" width=0.3 style=filled fillcolor="#166534"];',
        '  __end [shape=doublecircle label="" width=0.3 style=filled fillcolor="#991b1b"];',
    ]

    for activity_id, count in graph.activity_counts.most_common():
        name = graph.name(activity_id)
        lines.append(
            f'  "{html.escape(name)}" [label="{html.escape(name)}\\n{count:,}" '
            f'fillcolor="{_heat(count, max_activity)}"];'
        )

    for activity_id, count in graph.starts.most_common():
        lines.append(
            f'  __start -> "{html.escape(graph.name(activity_id))}" '
            f'[label="{count:,}" color="#166534" style=dashed];'
        )

    for (source, target), stats in sorted(graph.edges.items(), key=lambda item: -item[1].count):
        text = (
            f"{stats.count:,}"
            if annotate == "frequency"
            else f"{_duration(stats.median_seconds)}\\nn={stats.count:,}"
        )
        # A tie-broken-heavy edge is an edge whose direction we partly invented.
        suspect = stats.count and stats.tie_broken / stats.count > 0.5
        style = ' style="dotted"' if suspect else ""
        lines.append(
            f'  "{html.escape(graph.name(source))}" -> "{html.escape(graph.name(target))}" '
            f'[label="{text}" penwidth={_penwidth(stats.count, max_edge):.2f}'
            f' color="#1d4ed8"{style}];'
        )

    for activity_id, count in graph.ends.most_common():
        lines.append(
            f'  "{html.escape(graph.name(activity_id))}" -> __end '
            f'[label="{count:,}" color="#991b1b" style=dashed];'
        )

    lines.append("}")
    return "\n".join(lines)


def write_dot(graph: DFG, path: str | Path, **kwargs) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(dfg_to_dot(graph, **kwargs))
    return destination


# ---------------------------------------------------------------------------
# Hand-written SVG. Only for small, filtered graphs -- see the module docstring.

MAX_SVG_NODES = 15


def dfg_to_svg(graph: DFG, *, title: str | None = None) -> str:
    """Lay out a small DFG as SVG, with no external renderer.

    Layout is a longest-path layering: a node's row is the length of the
    longest path reaching it, which is a decent proxy for process order and is
    cheap. It is honest about its limits -- it refuses a graph too large to
    draw legibly rather than producing spaghetti.
    """
    activities = list(graph.activity_counts)
    if len(activities) > MAX_SVG_NODES:
        raise ValueError(
            f"{len(activities)} activities is too many to lay out legibly "
            f"(limit {MAX_SVG_NODES}). Filter the graph first -- "
            "DFG.filter_edges(keep_fraction=...) -- or render DOT instead."
        )

    successors: dict[int, set[int]] = {a: set() for a in activities}
    for (source, target) in graph.edges:
        if source != target and source in successors:
            successors[source].add(target)

    # Longest-path depth, with a visited set so a cycle cannot recurse forever.
    depth: dict[int, int] = {}

    def compute(activity: int, stack: frozenset[int]) -> int:
        if activity in depth:
            return depth[activity]
        if activity in stack:
            return 0
        best = 0
        for predecessor in graph.predecessors(activity):
            if predecessor == activity or predecessor not in successors:
                continue
            best = max(best, compute(predecessor, stack | {activity}) + 1)
        depth[activity] = best
        return best

    for activity in activities:
        compute(activity, frozenset())

    rows: dict[int, list[int]] = {}
    for activity in sorted(activities, key=lambda a: (depth[a], -graph.activity_counts[a])):
        rows.setdefault(depth[activity], []).append(activity)

    node_width, node_height = 190, 44
    gap_x, gap_y = 36, 76
    width = max(len(row) for row in rows.values()) * (node_width + gap_x) + gap_x
    height = len(rows) * (node_height + gap_y) + gap_y + 30

    centres: dict[int, tuple[float, float]] = {}
    for row_index, row in sorted(rows.items()):
        offset = (width - len(row) * (node_width + gap_x) + gap_x) / 2
        for column, activity in enumerate(row):
            x = offset + column * (node_width + gap_x)
            y = gap_y + row_index * (node_height + gap_y)
            centres[activity] = (x + node_width / 2, y + node_height / 2)

    max_edge = max((stats.count for stats in graph.edges.values()), default=1)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="100%" role="img" aria-label="{html.escape(title or graph.log.log_id)} process map">',
        "<style>"
        ".mb-node{fill:var(--mb-node,#eff6ff);stroke:var(--mb-stroke,#1d4ed8);stroke-width:1.2}"
        ".mb-label{font:500 12px system-ui,sans-serif;fill:var(--mb-ink,#0f172a);text-anchor:middle}"
        ".mb-sub{font:400 10px system-ui,sans-serif;fill:var(--mb-muted,#475569);text-anchor:middle}"
        ".mb-edge{stroke:var(--mb-stroke,#1d4ed8);fill:none;opacity:.75}"
        ".mb-edge-label{font:400 9px system-ui,sans-serif;fill:var(--mb-muted,#475569);text-anchor:middle}"
        "@media (prefers-color-scheme:dark){svg{--mb-node:#15233b;--mb-stroke:#60a5fa;"
        "--mb-ink:#e2e8f0;--mb-muted:#94a3b8}}"
        "</style>",
        '<defs><marker id="mb-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="currentColor" class="mb-edge"/></marker></defs>',
    ]
    if title:
        parts.append(
            f'<text x="{width / 2:.0f}" y="24" class="mb-label">{html.escape(title)}</text>'
        )

    for (source, target), stats in graph.edges.items():
        if source not in centres or target not in centres:
            continue
        if source == target:
            cx, cy = centres[source]
            parts.append(
                f'<path class="mb-edge" marker-end="url(#mb-arrow)" '
                f'd="M{cx + 70:.0f},{cy - 12:.0f} a 26 20 0 1 1 0,24" '
                f'stroke-width="{_penwidth(stats.count, max_edge):.1f}"/>'
            )
            parts.append(
                f'<text class="mb-edge-label" x="{cx + 118:.0f}" y="{cy + 4:.0f}">'
                f"{stats.count:,}</text>"
            )
            continue
        x1, y1 = centres[source]
        x2, y2 = centres[target]
        start_y = y1 + node_height / 2 if y2 > y1 else y1 - node_height / 2
        end_y = y2 - node_height / 2 if y2 > y1 else y2 + node_height / 2
        parts.append(
            f'<path class="mb-edge" marker-end="url(#mb-arrow)" '
            f'd="M{x1:.0f},{start_y:.0f} C{x1:.0f},{(start_y + end_y) / 2:.0f} '
            f'{x2:.0f},{(start_y + end_y) / 2:.0f} {x2:.0f},{end_y:.0f}" '
            f'stroke-width="{_penwidth(stats.count, max_edge):.1f}"/>'
        )
        parts.append(
            f'<text class="mb-edge-label" x="{(x1 + x2) / 2:.0f}" '
            f'y="{(start_y + end_y) / 2:.0f}">{stats.count:,}</text>'
        )

    for activity, (cx, cy) in centres.items():
        name = graph.name(activity)
        parts.append(
            f'<rect class="mb-node" x="{cx - node_width / 2:.0f}" y="{cy - node_height / 2:.0f}" '
            f'width="{node_width}" height="{node_height}" rx="8"/>'
        )
        shown = name if len(name) <= 26 else name[:25] + "…"
        parts.append(
            f'<text class="mb-label" x="{cx:.0f}" y="{cy - 2:.0f}">{html.escape(shown)}</text>'
        )
        parts.append(
            f'<text class="mb-sub" x="{cx:.0f}" y="{cy + 13:.0f}">'
            f"{graph.activity_counts[activity]:,}</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)


def write_svg(graph: DFG, path: str | Path, **kwargs) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(dfg_to_svg(graph, **kwargs))
    return destination
