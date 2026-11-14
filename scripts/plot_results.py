"""Rebuild E1 SVG figures using only the Python standard library."""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from run_mechanism import read_csv


def chart(
    path: Path, title: str, labels: list[str], series: list[tuple[str, list[float]]],
    y_label: str,
) -> None:
    colors = ["#1665a8", "#d35400", "#228833", "#993399", "#555555", "#c0392b"]
    maximum = max(v for _, values in series for v in values) * 1.1 or 1
    x = lambda i: 90 + i * 510 / max(1, len(labels) - 1)
    y = lambda value: 320 - value / maximum * 245
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="410" viewBox="0 0 900 410">',
        '<rect width="900" height="410" fill="white"/>',
        '<g font-family="sans-serif" font-size="13" fill="#222">',
        f'<text x="60" y="28" font-size="18">{html.escape(title)}</text>',
        f'<text x="30" y="55">{html.escape(y_label)}</text>',
        '<path d="M90 75V320H600" stroke="#222" fill="none"/>',
    ]
    for tick in range(6):
        value = maximum * tick / 5
        parts.append(f'<text x="10" y="{y(value) + 4:.1f}">{value:.3g}</text>')
        parts.append(f'<path d="M90 {y(value):.1f}H600" stroke="#ddd" fill="none"/>')
    for i, label in enumerate(labels):
        parts.append(f'<text x="{x(i) - 15:.1f}" y="345">{html.escape(label)}</text>')
    for index, (name, values) in enumerate(series):
        color = colors[index % len(colors)]
        points = " ".join(f"{x(i):.1f},{y(value):.1f}" for i, value in enumerate(values))
        parts.append(f'<polyline points="{points}" stroke="{color}" stroke-width="2" fill="none"/>')
        for i, value in enumerate(values):
            parts.append(f'<circle cx="{x(i):.1f}" cy="{y(value):.1f}" r="3" fill="{color}"/>')
        parts.append(f'<text x="625" y="{85 + index * 25}" fill="{color}">{html.escape(name)}</text>')
    parts.append('<text x="90" y="390">Warm-cache local protocol; logical reads, not physical disk I/O.</text>')
    parts.append('</g></svg>')
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def plot(summary: Path, output: Path) -> None:
    rows = read_csv(summary)
    if len(rows) != 24:
        raise ValueError("Plot input must contain 24 E1 configurations")
    output.mkdir(parents=True, exist_ok=True)
    for n in (100000, 1000000):
        by_g = {
            g: sorted((r for r in rows if int(r["dataset_rows"]) == n
                       and int(r["granule_rows"]) == g), key=lambda r: int(r["query_id"]))
            for g in (512, 2048, 8192)
        }
        if any(len(group) != 4 for group in by_g.values()):
            raise ValueError("Missing granule/query configuration")
        labels = [f"{float(r['actual_match_ratio']) * 100:g}%" for r in by_g[512]]
        chart(output / f"scan-ratio-N{n}.svg", f"N={n}: match ratio vs scan ratio", labels,
              [(f"G={g}", [float(r["scan_ratio"]) for r in group]) for g, group in by_g.items()],
              "scanned rows / N")
        chart(output / f"elapsed-N{n}.svg", f"N={n}: median query elapsed time", labels,
              [(f"{method} G={g}", [float(r[f"{method}_median_s"]) * 1000 for r in group])
               for g, group in by_g.items() for method in ("full_scan", "sparse_index")],
              "milliseconds")
        chart(output / f"index-space-N{n}.svg", f"N={n}: granule size vs index space",
              [str(g) for g in by_g],
              [("index", [float(group[0]["index_bytes"]) for group in by_g.values()])],
              "index file bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    plot(args.summary, args.output)
