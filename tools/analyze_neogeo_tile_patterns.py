#!/usr/bin/env python3
"""Analyze existing indexed frames as compression proxies, not detail evidence.

Validation uses assertions; optimized Python (-O) is explicitly rejected.
Reports contain counts and hashes, never pixel patterns or palette color lists.
"""

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import itertools
import json
from pathlib import Path
import struct
import sys


SHAPES = {"horizontal2": (2, 1), "block2x2": (2, 2)}
MODES = {"Low": (40, 28), "Mid": (53, 37), "High": (80, 56)}
FIXED_PALETTES = [set(range(i, min(i + 15, 256))) for i in range(0, 256, 15)]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def palettes(patterns):
    """Greedy extra 15-color palettes, preserving all 18 existing palettes."""
    sets = Counter()
    for pattern, count in patterns.items():
        sets[tuple(sorted(set(pattern)))] += count
    extra = []
    for colors, count in sorted(sets.items(), key=lambda item: (-len(item[0]), -item[1], item[0])):
        colors = set(colors)
        if any(colors <= p for p in FIXED_PALETTES + extra):
            continue
        choices = [i for i, p in enumerate(extra) if len(p | colors) <= 15]
        if choices:
            i = min(choices, key=lambda j: (len(colors - extra[j]), len(extra[j] | colors), j))
            extra[i].update(colors)
        else:
            extra.append(colors)
    assert all(any(set(p) <= pal for pal in FIXED_PALETTES + extra) for p in patterns)
    return extra


def summarize(patterns):
    total = sum(patterns.values())
    same = Counter({p: n for p, n in patterns.items() if len({c // 15 for c in p}) == 1})
    uniform = sum(n for p, n in patterns.items() if len(set(p)) == 1)
    same_count = sum(same.values())
    extra = palettes(patterns)
    return {
        "blocks": total,
        "same_group_blocks": same_count,
        "same_group_fraction": same_count / total,
        "uniform_blocks": uniform,
        "uniform_fraction": uniform / total,
        "nonuniform_blocks": total - uniform,
        "same_group_nonuniform_fraction": (same_count - uniform) / (total - uniform) if total > uniform else None,
        "unique_exact_ordered_patterns": len(patterns),
        "unique_same_group_slot_patterns": len({tuple(c % 15 + 1 for c in p) for p in same}),
        "unique_color_sets": len({tuple(sorted(set(p))) for p in patterns}),
        "existing_groups_used": sorted({c // 15 for p in patterns for c in p}),
        "distinct_colors_per_block_histogram": dict(sorted(Counter({k: sum(n for p, n in patterns.items() if len(set(p)) == k) for k in range(1, len(next(iter(patterns))) + 1)}).items())),
        "greedy_extra_palettes": len(extra),
        "greedy_total_palettes": 18 + len(extra),
        "greedy_extra_palette_bytes": 32 * len(extra),
        "static_dictionary_uncovered_patterns": sum(not any(set(p) <= pal for pal in FIXED_PALETTES + extra) for p in patterns),
    }


def blocks(pixels, width, height, bw, bh, sliding=False):
    return Counter(tuple(pixels[(y + dy) * width + x + dx]
                         for dy in range(bh) for dx in range(bw))
                   for y in range(0, height - bh + 1, 1 if sliding else bh)
                   for x in range(0, width - bw + 1, 1 if sliding else bw))


def texture_study(repo):
    sys.path.insert(0, str(repo / "tools"))
    from gen_neogeo_wall_columns import generate, lump_data
    from gen_neogeo_sprite_defs import read_embedded_wad_header
    header = repo / "doom64ng.h"
    wad = read_embedded_wad_header(header)
    bases, refs, columns = generate(wad)
    data = lump_data(wad, "TEXTUREP")
    metadata = []
    for i in range(len(bases)):
        offset = struct.unpack_from(">H", data, 2 * i)[0]
        _, width, height, overlap, count = struct.unpack_from(">HHHBB", data, offset)
        metadata.append((width, height, overlap))
    pieces = {}
    for length in (4, 16):
        patterns = Counter(tuple(col[y:y + length]) for col in columns
                           for y in range(0, 128, length))
        pieces[str(length)] = {
            "aligned_pieces": sum(patterns.values()),
            "unique_indexed_pieces": len(patterns),
            "unique_piece_crom_bytes_one_tile_each": len(patterns) * 128,
            "same_existing_group_fraction": sum(n for p, n in patterns.items() if len({c // 15 for c in p}) == 1) / sum(patterns.values()),
            "at_most_15_colors_fraction": sum(n for p, n in patterns.items() if len(set(p)) <= 15) / sum(patterns.values()),
            "unique_color_sets": len({tuple(sorted(set(p))) for p in patterns}),
        }
    return {
        "source_sha256": sha(header.read_bytes()),
        "texture_count": len(metadata),
        "declared_texture_columns": sum(w for w, h, o in metadata),
        "declared_texels": sum(w * h for w, h, o in metadata),
        "raw_texture_16x16_tile_slots_no_dedup": sum(((w + 15) // 16) * ((h + 15) // 16) for w, h, o in metadata),
        "generated_overlapped_textures": sum(o != 0 for w, h, o in metadata),
        "generated_column_references": len(refs),
        "unique_padded_columns": len(columns),
        "overlapped_height_histogram": dict(sorted(Counter(h for w, h, o in metadata if o).items())),
        "pieces": pieces,
        "baked_224_rows_one_column_per_tile_width_bytes_per_scale_phase": len(columns) * 14 * 128,
        "baked_224_rows_16_scales_16_phases_bytes": len(columns) * 14 * 128 * 16 * 16,
        "limitations": "Padded generated overlapped columns only; not complete walls, lighting, scale/phase, clipping or sprites. Zero padding counted. Tile-per-piece cost ignores palette feasibility/remapping and reuse across flips. No fit claim.",
    }


def main():
    if not __debug__:
        raise SystemExit("Validation requires assertions; do not use Python -O.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = (args.input / "results.json").read_bytes()
    records = json.loads(raw)
    expected = set(itertools.product((1, 2), MODES, (0, 90, 180, 270), (0, 1), (0, 1, 2)))
    grouped = defaultdict(list)
    observed = set()
    for r in records:
        key = (r["map"], r["mode"], r["yaw"], r["repeat"], r["sample"])
        assert key in expected and key not in observed, key
        observed.add(key)
        w, h = MODES[r["mode"]]
        assert (r["width"], r["height"]) == (w, h)
        directory = args.input / f'E1M{r["map"]}-{r["mode"]}-{r["repeat"]}'
        stem = f'{r["yaw"]}-{r["sample"]}'
        fb = (directory / (stem + ".framebuffer")).read_bytes()
        px = (directory / (stem + ".pixels")).read_bytes()
        assert len(fb) == 4480 and len(px) == w * h
        assert sha(fb) == r["framebuffer_sha256"] and sha(px) == r["sha256"]
        assert px == bytes(fb[x * 56 + y] for y in range(h) for x in range(w))
        grouped[key[:3]].append((r, px))
    assert observed == expected
    views, aggregate = [], defaultdict(Counter)
    view_patterns = defaultdict(list)
    for (game_map, mode, yaw), samples in sorted(grouped.items()):
        variants = len({r["sha256"] for r, px in samples})
        for sample in range(3):
            assert len({r["sha256"] for r, px in samples if r["sample"] == sample}) == 1
        r, px = next((r, px) for r, px in samples if r["repeat"] == 0 and r["sample"] == 1)
        w, h = MODES[mode]
        for shape, (bw, bh) in SHAPES.items():
            for alignment in ("aligned", "sliding"):
                patterns = blocks(px, w, h, bw, bh, alignment == "sliding")
                aggregate[(mode, shape, alignment)].update(patterns)
                aggregate[("All", shape, alignment)].update(patterns)
                view_patterns[(mode, shape, alignment)].append(patterns)
                view_patterns[("All", shape, alignment)].append(patterns)
                row = {"map": game_map, "mode": mode, "yaw": yaw, "shape": shape,
                       "alignment": alignment, "width": w, "height": h,
                       "warm_sample_sha256": r["sha256"], "sample_pixel_variants": variants,
                       "excluded_edge_pixels": w * h - (w // bw) * (h // bh) * bw * bh if alignment == "aligned" else None}
                row.update(summarize(patterns))
                views.append(row)
    totals = []
    for (mode, shape, alignment), patterns in sorted(aggregate.items()):
        row = {"mode": mode, "shape": shape, "alignment": alignment}
        row.update(summarize(patterns))
        members = [v for v in views if v["shape"] == shape and v["alignment"] == alignment and (mode == "All" or v["mode"] == mode)]
        dictionary = FIXED_PALETTES + palettes(patterns)
        row["static_dictionary_worst_view_coverage_fraction"] = min(
            sum(n for p, n in aggregate_view.items() if any(set(p) <= pal for pal in dictionary)) / sum(aggregate_view.values())
            for aggregate_view in view_patterns[(mode, shape, alignment)])
        row["view_mean_same_group_fraction"] = sum(v["same_group_fraction"] for v in members) / len(members)
        row["view_range_same_group_fraction"] = [min(v["same_group_fraction"] for v in members), max(v["same_group_fraction"] for v in members)]
        row["per_view_extra_palette_range"] = [min(v["greedy_extra_palettes"] for v in members), max(v["greedy_extra_palettes"] for v in members)]
        totals.append(row)
    report = {
        "input": str(args.input), "results_sha256": sha(raw),
        "validated_records": len(records), "distinct_views": len(grouped),
        "selection": "repeat 0, warm sample 1; all records/dump hashes and layout checked, repeated samples compared",
        "scope": "Existing framebuffer compressibility proxy ONLY. Not higher-resolution samples, quality, CPU or RAM proof. No more sprites assumed.",
        "palette_model": "color//15; slot=color%15+1; aligned blocks from (0,0), odd right/bottom fringes excluded; sliding windows sensitivity check. Greedy extra palette counts are constructive upper bounds, NOT minima or runtime implementations.",
        "codebooks": {"horizontal2": {"tiles": 225, "crom_bytes": 225 * 128}, "block2x2": {"tiles": 50625, "crom_bytes": 50625 * 128}},
        "views": views, "aggregate": totals, "textures": texture_study(args.repo),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    fields = [k for k, v in views[0].items() if not isinstance(v, (dict, list))]
    with (args.output / "views.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(views)
    print(f"Validated {len(records)} captures; analyzed {len(grouped)} views")
    for row in totals:
        if row["alignment"] != "aligned":
            continue
        print(f'{row["mode"]:4} {row["shape"]:12} same={row["same_group_fraction"]:.2%} '
              f'nonuniform_same={row["same_group_nonuniform_fraction"]:.2%} '
              f'unique={row["unique_exact_ordered_patterns"]} slots={row["unique_same_group_slot_patterns"]} '
              f'extra_palettes_union={row["greedy_extra_palettes"]} per_view={row["per_view_extra_palette_range"]}')
    print(json.dumps(report["textures"], indent=2))


if __name__ == "__main__":
    main()
