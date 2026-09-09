# Exact Neo Geo wall projection

Baseline: `31479a93bd3cff9775f8c2226ddda57f648858cd`.
Candidate: `8607a840c2a7692c46b1270a5f8934d3aaba5705`.

The Neo Geo wall-column loop now calculates the required high word with two
inline `MULU.W` instructions instead of calling the generic, three-multiply
`__mulsi3` routine. The helper preserves the wrapped product bits for positive
and negative distances and tangents. Other platforms keep their existing
projection expression. No resolution, texture sampling, visibility, enemy,
map, sound, or sprite-upload settings changed.

## Measured result

Warm renderer cycles, excluding interrupt-handler instructions, averaged over
four equally weighted orientations at each map's spawn position:

| Map | Detail | Baseline cycles | Candidate cycles | Reduction |
| --- | --- | ---: | ---: | ---: |
| E1M1 | Low | 470435 | 465086 | 1.14% |
| E1M1 | Medium | 565142 | 558106 | 1.24% |
| E1M1 | High | 795427 | 784708 | 1.35% |
| E1M2 | Low | 537144 | 529736 | 1.38% |
| E1M2 | Medium | 652539 | 642593 | 1.52% |
| E1M2 | High | 925540 | 910474 | 1.63% |

These are **GnGeo renderer-only modeled cycle reductions, not gameplay FPS
improvements or hardware measurements**. Generator68k uses static opcode cost
tables, not a bus-accurate 68000 model. Game logic, HUD, framebuffer upload and
presentation are outside the measured interval. Four spawn orientations are
not a representative distribution of a complete moving-game session.

## Validation

- Full Neo Geo ROM build succeeds with GCC 15.3.0. ROM use increases by 116
  bytes; RAM use is unchanged (56364 BSS + 356 data bytes).
- The host test checks 271253504 products against a 64-bit reference: all
  65536 signed 16-bit distances paired with all 4096 actual tangent entries,
  plus boundary and pseudorandom 32-bit operands. Undefined-behavior sanitizer
  is enabled. This checks the C fallback; target assembly is checked separately.
- Target disassembly confirms two inline multiplies in the wall-projection
  path and no generic multiplication call there.
- Emulator matrix: E1M1/E1M2, Low/Medium/High, four relative angles, three
  samples per angle, two fresh boots per case: **144 paired samples**.
- All pairs match both active-pixel and complete 4480-byte framebuffer SHA-256
  hashes, camera pose, and dimensions. Both builds repeat exactly across fresh
  boots. Every tested view has lower renderer cycle counts.
- The final candidate P1/P2 ROM hashes match the measured snapshot. All other
  game ROM component hashes match the baseline.
- The exported emulator-build script was run in a fresh directory. The
  repository's benchmark client reproduced the candidate E1M1 High smoke
  test's exact cold/warm cycle counts and both framebuffer hashes with it.

The framebuffer comparison is before HUD/upload/presentation. It is not an RGB
screenshot comparison, a hardware test, or coverage of every map and state.

## Reproduce

Run the standalone arithmetic check from the repository root:

```sh
python3 tools/verify_neogeo_projection.py
```

The [benchmark instructions](../../../tools/renderer_benchmark/README.md)
describe the private instrumented emulator, exact scene setup, cycle counters,
hashes, and comparison commands. The instrumentation is test-only; it is not
part of the game ROM or the normal emulator installation.

[Paired results](paired.json) retain every cold/warm sample, pose, cycle count,
and both hashes. [Metadata](metadata.json) records source revisions, executed
binary hashes, clock/settings, and harness provenance. No game binaries, BIOS,
WAD, or pixel dumps are included.

## Deferred experiment

Direct ROM access for precomposed wall columns was investigated but is not
enabled. Nine composed textures are shorter than 128 texels, while the sampler
can index 0..127. Existing cache entries can retain bytes above texture height;
ROM padding is zero. Even bypassing only full-height columns changes cache
history that shorter columns may observe. Defining those short-texture
semantics and validating them is a separate correctness task, not a safe
copy-elimination optimization.
