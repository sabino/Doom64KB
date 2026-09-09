# Exact Neo Geo masked-post projection

Baseline: `f862ec0f6da0b4422277751c37fd03ca5a5ad1fe` (the existing PR head,
already containing the wall-projection optimization).
Candidate: `282df55cdf8adb863980c1bb36b7fd1126373962`.

The Neo Geo masked-column routine now multiplies byte-sized post offsets and
lengths by a 32-bit scale with two inline `MULU.W` instructions per product,
instead of the generic three-multiply helper. Arithmetic preserves the wrapped
32-bit result, including clipping-boundary cases. An early return avoids walking
posts when the floor and ceiling clips leave no visible row. Other platforms
retain their existing code.

No resolution, sprite budget, visibility distance, object population, map,
texture sampling, audio, menu or upload behavior was changed.

## Measured result

Warm renderer cycles, excluding interrupt-handler instructions, averaged across
four equally weighted spawn orientations:

| Map | Detail | Previous PR cycles | Candidate cycles | Further reduction |
| --- | --- | ---: | ---: | ---: |
| E1M1 | Low | 465086 | 461370 | 0.80% |
| E1M1 | Medium | 558106 | 552146 | 1.07% |
| E1M1 | High | 784708 | 775203 | 1.21% |
| E1M2 | Low | 529736 | 521998 | 1.46% |
| E1M2 | Medium | 642593 | 631390 | 1.74% |
| E1M2 | High | 910474 | 892288 | 2.00% |

These are **modeled renderer-only cycle reductions, not gameplay FPS or physical
hardware timings**. GnGeo's Generator68k uses static opcode costs. The interval
excludes game logic, HUD, sprite upload and presentation. Stationary spawn views
do not represent a complete level or moving combat.

## Validation

- Full Neo Geo build with GCC 15.3.0. Program size decreases by 92 bytes;
  RAM is unchanged at 56364 BSS + 356 data bytes.
- `tools/verify_neogeo_posts.py` checks 2060800 products against a wide unsigned
  reference, 3092750 cases using the extracted production masked-column routine,
  and 99453 closed-clip cases with no post reads or drawing callbacks.
- GCC and Clang host tests pass with fatal undefined-behavior sanitizer checks.
  These exercise the C fallback, not the target inline assembly.
- Target disassembly confirms four inline multiplies per post rather than six,
  with no generic product helper calls in this routine.
- All 144 paired emulator samples match camera pose, dimensions, active-pixel
  SHA-256 and full 4480-byte framebuffer SHA-256. Fresh-boot repeats are exact.
  Every tested view uses fewer renderer cycles.

The [paired results](paired.json) and [metadata](metadata.json) identify the exact
executed binaries and retain all cold/warm samples. No game data or binaries are
included. Hashes are taken before HUD/upload/presentation; this is not RGB
screenshot validation or coverage of every game state.

## Reproduce

```sh
python3 tools/verify_neogeo_posts.py
CC=clang python3 tools/verify_neogeo_posts.py
```

Use the [isolated emulator benchmark](../../../tools/renderer_benchmark/README.md)
for target timing and framebuffer comparisons. The normal emulator installation
and user game are not controlled by this benchmark.

## Rejected experiments

- Aligned longword flat fills regressed all eight tested High-detail views.
  Their standalone target correctness batch was cancelled after the production
  timing regression; it did not produce a correctness pass.
- Outlining the wall loop produced only a small spawn-view change, insufficient
  evidence to keep the additional compiler-specific code in this round.
- Caching the reciprocal on constant-scale segments and omitting it for
  masked-only segments increased E1M2 High spawn cycles from 1778784 to 1790480
  versus the posts-only candidate. That experiment was removed.

These rejected changes are not in the game build.
