# More detail within the existing sprite budget

Research only. The playable renderer still uses its existing detail modes.
No higher-resolution mode, new palette allocation or weapon overlay is enabled.

## Proposed encoding

Instead of using a solid-color C-ROM tile for each microframebuffer cell, a tile
can contain two differently colored regions. The same four-pixel-wide cell could
then display two two-pixel-wide samples, using the same tilemap entry and sprite
strip. This changes the information inside each tile, not the sprite count.

With 15 visible palette slots, every ordered pair of slots needs only
15 * 15 = 225 tiles, or 28800 bytes of C-ROM. This codebook is shared across
palettes, not duplicated per palette. Four-sample 2x2 patterns need 50625 tiles,
or 6480000 bytes, before other graphics and allocation constraints.

Neo Geo tiles have one palette attribute each; shrinking skips source pixels
and cannot invent detail. See the [sprite format](https://wiki.neogeodev.org/index.php/Sprites)
and [shrinking rules](https://wiki.neogeodev.org/index.php/Sprite_shrinking).
Producing additional source samples and choosing encodings still costs CPU.

## Corpus analysis

The reusable `tools/analyze_neogeo_tile_patterns.py` validates the complete
144-capture benchmark input and studies 24 distinct views: E1M1/E1M2,
Low/Medium/High, four spawn orientations. It selects warm sample 1 from repeat 0.
The existing framebuffer layout and all dump hashes are checked first.

One deterministic shared palette dictionary covers each entire observed corpus:

| Samples per tile | Alignment | Extra palettes | Total including existing 18 |
| --- | --- | ---: | ---: |
| Two horizontal | Aligned | 51 | 69 |
| Two horizontal | Every sliding position | 65 | 83 |
| Four, 2x2 | Aligned | 128 | 146 |
| Four, 2x2 | Every sliding position | 239 | 257 |

These are constructive greedy upper bounds, not minimum palette requirements.
Coverage is 100% for these input patterns only. Sliding positions include the
aligned ones and test sensitivity to cell boundaries. Odd Medium-mode fringes
are excluded from aligned blocks, not silently padded.

The current allocation reserves FIX palettes 0-15, microframebuffer 16-33,
backgrounds 34-49, and **64-191 as CPU column-cache storage**. Reserving palette
255 for the backdrop leaves 77 whole unused banks. The sliding-pair dictionary
needs 65 extra banks, or 2080 bytes of palette RAM. It fits this accounting;
the measured 2x2 dictionary does not. Integration still needs explicit ownership
and correct damage/bonus PLAYPAL updates.

Existing palette groups alone are unsuitable for preserving detail: among
nonuniform aligned blocks, only 3.69% of horizontal pairs and 1.17% of 2x2 blocks
share a current group. The apparently high overall coverage comes mostly from
solid-color regions.

**This is a compressibility study of existing low-resolution pixels, not proof
of higher-resolution quality or performance.** Unseen maps, camera positions,
animation and lighting may produce additional pairs. Arbitrary pairs from all
256 indices require at least ceil(C(256,2)/C(15,2)) = 311 palettes, so no fixed
256-palette scheme can guarantee exact coverage of every possible pair.

## Prototype requirements

Start with pairs, a static dictionary, deterministic palette-slot ordering, and
a same-footprint coarse fallback for unseen pairs. Do not spend extra sprites
on exceptions. A dense 16-bit lookup for all ordered byte pairs costs 128 KiB of
CPU-addressable ROM; sparse lookup is smaller but needs its runtime cost measured.
C-ROM capacity alone does not solve CPU lookup storage.

Actual 160x56 source samples would require an 8960-byte indexed framebuffer,
versus today's 4480 bytes, before other renderer arrays. A 160x112 buffer would
need 17920 bytes. Selective detail or streaming would need a separate memory
design. Upscaling the current framebuffer would not provide new detail.

Validate native GnGeo and the FBNeo-compatible strip profile, shrink phase,
palette effects, HUD/menu/wipe behavior, unseen scenes, CPU cycles and memory
before exposing a new mode. Low/Medium/High must retain their existing behavior.

## Alternative: weapon overlay

Native framebuffer IDs 1-320 and background IDs 321-358 leave ordinary sprite
IDs 359-380 free: 22 slots, with sprite 0 reserved. High uses 80 of the 96
scanline entries while backgrounds are hidden. A single unshrunk overlay up to
256 pixels wide can fit in the remaining 16 strips, positioned on its own grid.
Weapon and muzzle-flash layers must share that combined budget or be precomposed;
transparent pixels do not remove a strip's scanline-list cost.

This could replace software weapon pixel drawing with higher-detail C-ROM
artwork. It does not eliminate animation logic, positioning or hardware uploads.
All weapons, flash combinations, palette effects and background-dependent
invisibility fuzz need validation. The browser-compatible profile reserves all
380 ordinary IDs, so it needs different bank ownership rather than simply
adding the native overlay. This is budget analysis, not an implemented feature.

## Pre-scaled walls

The overlapped-wall generator contains 1251 unique padded columns. Aligned
four-texel pieces produce 6179 unique patterns: 790912 C-ROM bytes at one tile
each, before lighting, scale/phase and clipping. This is not a complete wall
atlas. A naive 224-row, one-column-per-tile-width atlas would use 2241792 bytes
per scale/phase; 16 scales and 16 phases already multiply that to 573898752
bytes before lighting. Small reusable pieces deserve testing; whole pre-scaled
walls are not a demonstrated ROM fit.

## Reproduce

First obtain the private dumps using the
[isolated renderer benchmark](../../../tools/renderer_benchmark/README.md).
Then run:

```sh
python3 tools/analyze_neogeo_tile_patterns.py \
  --input /path/to/baseline-results --output /tmp/tile-pattern-study
python3 tools/test_neogeo_tile_patterns.py
```

Both paths are required; the source repository defaults to the script's root.
Python `-O` is rejected because verification uses assertions.
[Results](results.json) contain counts, hashes and provenance only, not pixel
patterns, palette color lists, ROMs or WAD data.
