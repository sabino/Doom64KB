#!/usr/bin/env python3
"""Fail closed on missing coverage, nondeterminism, pose or pixel mismatches."""
import argparse
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path


def index(path):
    rows = json.loads(Path(path).read_text())
    indexed = {(r['map'], r['mode'], r['yaw'], r['sample'], r['repeat']): r for r in rows}
    assert len(indexed) == len(rows), 'duplicate result'
    return indexed


def compare(baseline, candidate):
    a, b = index(baseline), index(candidate)
    assert a and a.keys() == b.keys(), 'empty or unequal coverage'
    expected = {(m, mode, yaw, sample, repeat) for m in [1, 2]
                for mode in ['Low', 'Mid', 'High'] for yaw in [0, 90, 180, 270]
                for sample in range(3) for repeat in range(2)}
    assert a.keys() == expected, 'expected 2 maps x 3 modes x 4 yaws x 3 samples x 2 repeats'
    for label, data in [('baseline', a), ('candidate', b)]:
        for key, row in data.items():
            reference = data[(*key[:-1], 0)]
            for field in ['cycles', 'inclusive_cycles', 'irq_cycles', 'sha256', 'framebuffer_sha256', 'pose']:
                assert row[field] == reference[field], (label, 'not repeatable', key, field)
    for key in a:
        for field in ['sha256', 'framebuffer_sha256', 'pose', 'width', 'height']:
            assert a[key][field] == b[key][field], ('candidate mismatch', key, field)
    print(f'PASS: {len(a)} paired samples; exact fresh-process repeatability, poses and both hashes match.')
    print('Warm renderer cycles (IRQ excluded); average across four yaws and samples 1,2; not FPS.')
    print('| Map | Mode | Baseline cycles | Candidate cycles | Reduction | Baseline ms | Candidate ms |')
    print('|---|---|---:|---:|---:|---:|---:|')
    for m in [1, 2]:
        for mode in ['Low', 'Mid', 'High']:
            keys = [k for k in a if k[0:2] == (m, mode) and k[3] > 0 and k[4] == 0]
            ac = sum(a[k]['cycles'] for k in keys) / len(keys)
            bc = sum(b[k]['cycles'] for k in keys) / len(keys)
            print(f'| E1M{m} | {mode} | {ac:.0f} | {bc:.0f} | {(1-bc/ac)*100:.2f}% | {ac/12000:.3f} | {bc/12000:.3f} |')
    print('\nPer-view warm sample 1 (cycles baseline -> candidate; pixel SHA-256):')
    for key in a:
        if key[3:] == (1, 0):
            ar, br = a[key], b[key]
            print(f'E1M{key[0]} {key[1]} yaw+{key[2]}: {ar["cycles"]} -> {br["cycles"]}; {ar["sha256"]}')
    return a, b


def artifacts(args, a, b, summary):
    out = Path(args.artifact_dir)
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    for key, baseline in a.items():
        row = {name: baseline[name] for name in ['map', 'mode', 'yaw', 'repeat', 'sample']}
        row['pose'] = baseline['pose']
        for prefix, data in [('baseline', baseline), ('candidate', b[key])]:
            for name in ['cycles', 'inclusive_cycles', 'irq_cycles', 'sha256', 'framebuffer_sha256']:
                row[prefix + '_' + name] = data[name]
        rows.append(row)
    (out / 'paired.json').write_text(json.dumps(rows, separators=(',', ':')) + '\n')
    with (out / 'paired.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=[k for k in rows[0] if k != 'pose'])
        writer.writeheader()
        writer.writerows({k: v for k, v in row.items() if k != 'pose'} for row in rows)
    manifests = {label: json.loads(Path(path).with_name('manifest.json').read_text())
                 for label, path in [('baseline', args.baseline), ('candidate', args.candidate)]}
    assert manifests['baseline']['emulator'] == manifests['candidate']['emulator'], 'different emulator builds'
    settings = {}
    for label, path in [('baseline', args.baseline), ('candidate', args.candidate)]:
        command_files = sorted(Path(path).parent.glob('E1M*/command.json'))
        assert command_files, 'missing execution settings'
        # Remove machine-local paths from the shareable artifact.
        command = json.loads(command_files[0].read_text())
        command[command.index('-s') + 2] = '<private-instrumented-gngeo>'
        for flag, placeholder in [('-i', '<snapshot-rom-directory>'), ('-d', '<snapshot-gngeo_data.zip>')]:
            command[command.index(flag) + 1] = placeholder
        settings[label] = command
    root = Path(__file__).resolve().parent
    metadata = {
        'schema': 1,
        'description': 'Projection-only renderer microbenchmark; not end-to-end FPS or hardware bus timing',
        'source_commits': {'baseline': args.baseline_source_commit,
                           'candidate': args.candidate_source_commit,
                           'gngeo': '7709e966418a7e9f2e6aa4585ac5f41e2c4ccfb7',
                           'emudbg': '1bfd49fe87bf662e39a894ac0db23d589796dbf2'},
        'source_provenance_note': 'Game revisions supplied by investigator; ELF/ROM hashes identify executed inputs. Candidate final P1/P2 verified identical to earlier projection-only snapshot.',
        'manifests': manifests,
        'commands': settings,
        'environment': {'HOME': '<fresh-private-directory-per-repeat>', 'TMPDIR': '<private-directory>',
                        'SDL_AUDIODRIVER': 'dummy', 'SDL_RENDER_DRIVER': 'software', 'WAYLAND_DISPLAY': 'unset'},
        'counter': 'sum(cpu_68k_run_step()); generator68k static piib->clocks; 64-bit',
        'irq_classifier': 'SR.IPL != 0, sampled after pending delivery before instruction; no renderer masking found in inspected paths',
        'clock': {'68kclock_adjust_percent': 0, 'debug_timeslice_cycles': 200000, 'nominal_hz': 12000000},
        'scene': {'maps': [1, 2], 'modes': {'Low': [40, 28], 'Mid': [53, 37], 'High': [80, 56]},
                  'relative_yaws_degrees': [0, 90, 180, 270], 'skill': 2, 'gametic': 2,
                  'setup_ticks': 1, 'samples_per_yaw': 3, 'fresh_boot_repeats': 2,
                  'mobj_angle_offset': 32, 'column_stride': 56},
        'validation': {'paired_samples': len(rows), 'exact_repeats': True, 'poses_match': True,
                       'active_pixel_hashes_match': True, 'physical_framebuffer_hashes_match': True},
        'harness_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
                           ['bench.py', 'compare.py', 'build-emulator.sh', 'emulator-perf.patch', 'README.md']},
    }
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (out / 'summary.md').write_text('# Projection-Only GnGeo Results\n\n' + summary +
        '\nTiming uses static `piib->clocks`, not hardware bus traces. IRQ exclusion classifies '
        'nonzero SR.IPL; no masking was found in inspected renderer paths. Renderer-only '
        'nominal 12 MHz time, not end-to-end FPS. See README.md for the complete sampling contract.\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('baseline')
    parser.add_argument('candidate')
    parser.add_argument('--artifact-dir')
    parser.add_argument('--baseline-source-commit')
    parser.add_argument('--candidate-source-commit')
    args = parser.parse_args()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        a, b = compare(args.baseline, args.candidate)
    print(output.getvalue(), end='')
    if args.artifact_dir:
        artifacts(args, a, b, output.getvalue())
