# Bounded Neo Geo wall reciprocals

Final source: `7c4a774827ebfc1c9581de38d5c5ca456baa77d3`.
Previous PR baseline: `f862ec0f6da0b4422277751c37fd03ca5a5ad1fe`.
Posts-only intermediate: `282df55cdf8adb863980c1bb36b7fd1126373962`.

The wall-column loop knows that its scale lies in 256..4194304. Endpoint
scales are clamped by `R_ScaleFromGlobalAngle`; the truncated signed interpolation
step keeps every rendered column between those endpoints. A 65-byte bit-length
table can therefore normalize large scales directly, without calling the
general-purpose reciprocal helper and CLZ routine. Small scales retain the
same existing ROM table lookup. The result is exactly the existing approximate
reciprocal, shifted and narrowed as before, not a new approximation.

`neogeo/doom_reciprocal.h` is used only by this bounded Neo Geo column path.
Other reciprocal callers and other platforms are unchanged. The generated
tables now have external const linkage so this path shares their existing
definitions in `m_fixed.c`; the low table remains in `.text2`. There is no second
copy of either table and no additional RAM.

## Results

Warm renderer cycles, averaged equally across four spawn orientations:

| Map | Detail | Previous PR | Posts only | Final | Reduction vs previous PR |
| --- | --- | ---: | ---: | ---: | ---: |
| E1M1 | Low | 465086 | 461370 | 459175 | 1.27% |
| E1M1 | Medium | 558106 | 552146 | 548607 | 1.70% |
| E1M1 | High | 784708 | 775203 | 768204 | 2.10% |
| E1M2 | Low | 529736 | 521998 | 518830 | 2.06% |
| E1M2 | Medium | 642593 | 631390 | 627134 | 2.41% |
| E1M2 | High | 910474 | 892288 | 886592 | 2.62% |

The reciprocal change alone saves a further 0.48-0.90% versus posts-only in these
map/detail averages. Every tested view improves against both inputs.

These are **GnGeo modeled renderer cycles, not end-to-end FPS or real hardware
timings**. The test excludes interrupts, game logic, HUD, sprite upload and
presentation. Static opcode timing and stationary spawn views limit how closely
these numbers predict a full moving-game session.

## Validation

- Full Neo Geo ROM build succeeds with GCC 15.3.0.
- Final program size is 2053860 bytes: +164 versus posts-only, +72 versus the
  previous PR version. Data (356 bytes) and BSS (56364 bytes) are unchanged.
- Host GCC and Clang checks exhaust all 4194049 legal scales. They compare the
  production helper, extracted existing reciprocal functions and an independent
  normalization oracle using the real ROM table values, with fatal UBSan checks.
- All 65536 ROM table entries and all 65 normalization entries are checked.
  Tests also cover narrowing below scale 512, lookup discontinuities, index
  bounds, standalone header linkage, repeated inclusion and compile-time
  rejection of a missing or changed `COLEXTRABITS` contract.
- Regenerated tables retain their values, constness and bank placement; one
  exported object per table occupies 131072 + 65536 bytes, not two copies.
- Target disassembly confirms inline normalization without a reciprocal/CLZ
  call in this wall-column path. General callers still use their existing code.
- Both comparisons pass all 144 paired target samples: exact poses, dimensions,
  active-pixel and full-framebuffer hashes, and fresh-process repeatability.
- The earlier projection and masked-post host verifiers also still pass.

No gameplay settings, resolution, visibility, enemies, sound, menu, background
or upload behavior changed. These tests do not establish correctness of every
map/state, or validate the final RGB sprite/palette presentation pipeline.

## Reproduce

```sh
python3 tools/verify_neogeo_reciprocal.py
CC=clang python3 tools/verify_neogeo_reciprocal.py
python3 tools/verify_neogeo_posts.py
python3 tools/verify_neogeo_projection.py
```

Follow the [benchmark procedure](../../../tools/renderer_benchmark/README.md)
for target comparisons. [Paired results](paired.json) retain every sample against
the previous PR baseline; [metadata](metadata.json) identifies exact input
hashes and source revisions. The [posts-only report](../2026-09-09-posts/README.md)
provides the intermediate samples for incremental comparison. No ROMs, BIOS,
WADs or framebuffer dumps are included.

The separate [tile-pattern study](../2026-09-09-tile-patterns/README.md) explores
smaller pixels without increasing sprite counts. It is not enabled in this build.
