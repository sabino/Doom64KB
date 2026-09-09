# Deterministic GnGeo renderer benchmark

Reusable files: `bench.py`, `compare.py`, `build-emulator.sh`, and
`emulator-perf.patch`. The small test-only patch targets these source revisions:

- GnGeo: `7709e966418a7e9f2e6aa4585ac5f41e2c4ccfb7`
- emudbg: `1bfd49fe87bf662e39a894ac0db23d589796dbf2`

Do not publish copied emulator trees, ELF/ROM/WAD files, `.pixels`, or
`.framebuffer` dumps. Publish scripts, patch, notes, paired JSON, metadata and
summary only.

## Build A Private Emulator

Prerequisites: Linux, Python 3.8+, Xvfb/xvfb-run/xauth, GCC/make/autotools,
SDL2, OpenGL/GLEW and zlib development packages, ngdevkit toolchain and BIOS
assets, and local GnGeo/emudbg sources. No Python third-party packages.

```sh
bash build-emulator.sh /path/to/ngdevkit/source /tmp/private-gngeo /path/to/ngdevkit/prefix
```

This copies sources into a NEW destination, applies the small patch and builds
there. It does not install anything or modify its input source trees. The prefix
is read-only, for installed headers/libraries and default BIOS assets.

## Run

Reserve localhost TCP port 2159 exclusively. The harness refuses an occupied
port before launching its own process; it never attaches to a pre-existing
emulator. It runs sequentially, with private HOME and headless Xvfb, no joystick,
dummy audio, software blitter, no desktop interaction, and no service commands.
Never run baseline and candidate commands concurrently.

```sh
python bench.py --neogeo /path/to/baseline/neogeo --output /tmp/results-baseline \
  --emulator /tmp/private-gngeo/build-gngeo/src/gngeo --nm m68k-neogeo-elf-nm
python bench.py --neogeo /path/to/candidate/neogeo --output /tmp/results-candidate \
  --emulator /tmp/private-gngeo/build-gngeo/src/gngeo --nm m68k-neogeo-elf-nm
python compare.py /tmp/results-baseline/results.json /tmp/results-candidate/results.json
```

Add `--artifact-dir /tmp/shareable-results --baseline-source-commit COMMIT
--candidate-source-commit COMMIT` to `compare.py` to generate compact paired
CSV/JSON, metadata and summary without any ROM data or pixel dumps.

Outputs must not already exist. Each input directory must contain a matching
`DOOM64KB.elf` and `rom/` (including doom64kb.zip and its matching gngeo_data.zip).
Inputs are snapshotted before emulation. Use the same emulator binary for both.
The archive is what GnGeo loads; ensure its P1/P2 match the loose files and ELF.
The tested ROMs are projection-only, not the deferred direct-ROM experiment.

Quick smoke: append `--maps 1 --modes High --angles 0 --samples 2 --repeats 1`.
Use the default full matrix for `compare.py`, which fails closed on incomplete
coverage, duplicate records, nonrepeatability, pose or pixel differences.

## Scene And Sampling Contract

1. Fresh emulator and private HOME per map/mode/repeat. Break before first
   `G_Ticker`, after normal game startup initializes subsystems.
2. Clear demo playback, game action and menu-active state. Set gametic=1 and
   basetic=0. Invoke the unchanged ROM's `G_InitNew(sk_medium=2, map)` through
   emudbg register/stack writes, then one `G_Ticker` with no input; set gametic=2.
3. Set pending quality and invoke `NG_ApplyMicroFramebufferMode` normally.
   Verify actual dimensions: Low 40x28, Mid 53x37, High 80x56. This harness is
   for the native build, not the FBNeo safe-strips 76-column profile.
4. At the fixed spawn position, rotate relative to spawn yaw in order
   0, +90, +180, +270 degrees. No further gameplay ticks. The only raw structure
   offset is mobj.angle=32, verified against these ELF disassemblies and
   p_mobj.h; override `--mobj-angle-offset` for another ABI. Symbol names and
   sizes are resolved from each ELF independently, including LTO suffixes.
5. Invoke `R_RenderPlayerView` three times at each angle. Sample 0 includes
   cache warmup for that angle; 1 and 2 are warm. Retain the same texture-cache
   history across yaw changes. Repeat the whole process twice from fresh boot.

The client emulates a normal C call using the existing stack and a synthetic
return PC at 0xffff00. emudbg stops there BEFORE executing an instruction.
Registers are restored after each call; game RAM changes remain. This is
not input automation, a gameplay demo, or a representative moving-game workload.

## Timing, Not FPS

The patch adds a monotonic 64-bit counter of the actual `cycle` values returned
by `cpu_68k_run_step()` in `cpu_68k_dpg_step()`. It does NOT change instructions,
the clock configuration, cycle costs, or interrupt cadence. Debug mode uses its
existing 200,000-cycle frame timeslice. CLI explicitly requests stock 68k/Z80
clock adjustments (0), NTSC, no raster and no sound; GnGeo also forces sound off
in debug mode.

The measured interval starts before the renderer's first instruction and ends
after its RTS, before the return breakpoint executes. It excludes synthetic
call setup and all debugger round trips, host wall time, display throttling,
game logic, HUD/menu drawing, framebuffer upload and presentation waits.

`inclusive_cycles` includes interrupt-handler instructions executed during the
call. A second counter in `reg68k_external_step()` samples SR.IPL **after**
pending-interrupt delivery and **before** instruction execution. Costs with
IPL != 0 are `irq_cycles`; `cycles = inclusive_cycles - irq_cycles`. This removes
VBlank-handler phase noise for this renderer, which does not itself raise IPL.
It is an IPL-based classifier, not a general-purpose interrupt profiler. No
interrupt masking was found in the renderer paths inspected for this test.
Synthetic interrupt entry cycles are not added: this follows GnGeo's model.

`ms_at_12mhz = cycles / 12000` is nominal renderer-equivalent emulated time.
**It is not measured hardware latency and must not be converted to actual FPS.**
Generator68k uses static opcode cost tables (`piib->clocks`), not hardware bus
traces. These tables omit or
approximate hardware operand-dependent costs and bus behavior. Small gains
need hardware or a more accurate core to quantify precisely. The harness
measures this exact GnGeo core consistently, not a host instruction estimate.

## Hashes And Evidence

`sha256` hashes active indexed pixels in row-major order: source[x*56+y],
width*height bytes. `framebuffer_sha256` hashes all 4,480 physical column-major
bytes, including inactive padding. Both are captured immediately after the
renderer returns, before upload/HUD/presentation. Neither is an RGB screenshot
or a test of the sprite/palette upload pipeline. Pose and gametic are recorded
for every sample and must match across builds.

`manifest.json` records SHA-256 of the exact ELF, ROM inputs and emulator.
Per-run `command.json` captures settings; results retain cold and warm costs,
IRQ costs, dimensions, yaw, repeat/sample indices, pose and both hashes.
The build ELF/ROM hashes are authoritative; a Git HEAD alone cannot identify
a dirty worktree build. The compact paired artifact retains these hashes and
source revision provenance without containing any game binaries or pixels.

The summary averages warm samples over the four fixed angles with equal weight.
It is a view-set comparison, not a scene-frequency-weighted gameplay benchmark.
Matching this matrix does not prove correctness in all maps, positions, animated
states or texture-cache histories.

## Safety And Limitations

The patch adds only test instrumentation plus missing/bounded emudbg `m/M/P`
operations needed by this client; p12/p13 expose total cycles, p14/p15 expose
IPL cycles (register numbers are hex). The upstream packet server is minimal;
use only this single loopback client, with no other debugger connections.
Do not expose it on a network or treat this as production protocol hardening.

Each owned emulator process group is terminated and reaped before the next
launch; no broad process matching or system/user service management is used.
Use a normal Python interpreter (not `python -O`), since validation uses asserts.
