"""Rebuild native scan and all-query hot-time figures from E2/E3 logs."""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

from database_support import DBMS
from plot_results import chart
from run_mechanism import read_csv, sha256, write_json


def plot(suite: Path) -> None:
    output = suite / "figures"
    output.mkdir(exist_ok=True)
    native = read_csv(suite / "native-validation" / "queries.csv")
    chart(output / "native-read-rows.svg", "E2: native ClickHouse rows processed",
          [r["query_name"] for r in native],
          [("read_rows", [float(r["read_rows"]) for r in native])], "server read_rows")
    hot = read_csv(suite / "hot.csv")
    lookup = {(r["dbms"], int(r["query_id"])): r for r in hot}
    if len(lookup) != 129:
        raise ValueError("Hot plot requires all 43 queries for each of three DBMS")
    colors = dict(zip(DBMS, ("#1665a8", "#d35400", "#228833")))
    successful = [float(r["hot_elapsed_s"]) for r in hot if r["valid"] == "True"]
    if not successful:
        raise ValueError("No validated performance measurements to plot")
    bottom = math.floor(math.log10(min(successful)))
    top = max(2, math.ceil(math.log10(max(successful))))
    y = lambda elapsed: 420 - (math.log10(elapsed) - bottom) / (top - bottom) * 330
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="540" viewBox="0 0 1600 540">',
        '<rect width="1600" height="540" fill="white"/>',
        '<g font-family="sans-serif" font-size="12" fill="#222">',
        '<text x="60" y="30" font-size="19">E3: deterministic adapted ClickBench, hot=min(Run2,Run3)</text>',
        '<text x="20" y="65">seconds (log scale)</text>',
    ]
    for exponent in range(bottom, top + 1):
        value = 10**exponent
        parts += [f'<text x="10" y="{y(value) + 4:.1f}">{value:g}</text>',
                  f'<path d="M70 {y(value):.1f}H1530" stroke="#ddd"/>']
    for index, dbms in enumerate(DBMS):
        parts.append(f'<text x="{850 + index * 200}" y="60" fill="{colors[dbms]}">{html.escape(dbms)}</text>')
    for query_id in range(1, 44):
        x = 80 + (query_id - 1) * 34
        parts.append(f'<text x="{x}" y="445" transform="rotate(45 {x} 445)">Q{query_id}</text>')
        for index, dbms in enumerate(DBMS):
            row = lookup[(dbms, query_id)]
            if row["valid"] == "True":
                position = y(float(row["hot_elapsed_s"]))
                parts.append(f'<rect x="{x + index * 9}" y="{position:.1f}" width="7" '
                             f'height="{420 - position:.1f}" fill="{colors[dbms]}"/>')
            else:
                parts.append(f'<text x="{x + index * 9}" y="410" fill="#c0392b">x</text>')
    parts += [
        '<text x="70" y="495">All 43 queries retained. x = failed/unvalidated; no performance value.</text>',
        '<text x="70" y="520">Local 1M-row fixed shard; validation precedes timing; no cache clearing; not official scoreboard results.</text>',
        '</g></svg>',
    ]
    (output / "clickbench-hot.svg").write_text("\n".join(parts) + "\n")
    write_json(output / "manifest.json", {
        "source": {"hot.csv": sha256(suite / "hot.csv"),
                   "native-validation/queries.csv": sha256(suite / "native-validation" / "queries.csv")},
        "figures": {p.name: sha256(p) for p in sorted(output.glob("*.svg"))},
    })
    print("Generated two E2/E3 SVG figures from saved logs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    plot(parser.parse_args().suite)
