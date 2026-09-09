#!/usr/bin/env python3
"""Exhaustively host-check the production column reciprocal and real ROM tables.

Regenerates the tables in a temporary tree, then compiles the actual generated
definitions, new header and extracted m_fixed.c functions with UBSan. The
independent oracle normalizes with a loop and uses the original source table.
The host CLZ shim models the target's 32-bit unsigned long, not LP64's 64 bits.
Only temporary build files are written; no emulator or game build is run.
"""

import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]

PROGRAM = r'''
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include "production_column.h"
#include "rom_tables.h"
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wsign-compare"
#include "production_fixed.h"
#pragma GCC diagnostic pop

uint16_t column_probe(uint32_t v);

#define LENGTH(a) (sizeof(a) / sizeof((a)[0]))
_Static_assert(UINT_MAX == UINT32_MAX, "the CLZ shim needs 32-bit unsigned int");
_Static_assert(LENGTH(reciprocalLow) == 32768, "low ROM table length");
_Static_assert(LENGTH(reciprocalHigh) == 32768, "high ROM table length");
_Static_assert(LENGTH(original_reciprocal) == 65536, "source table length");
_Static_assert(LENGTH(ng_column_reciprocal_shift) == 65, "shift table length");

static void fail(const char *what, uint32_t v, uint32_t got, uint32_t expected)
{
    fprintf(stderr, "%s: v=%" PRIu32 " got=%" PRIu32 " expected=%" PRIu32 "\n",
            what, v, got, expected);
    exit(1);
}

static uint16_t reference(uint32_t v)
{
    unsigned shift = 0;
    while (v > 65535u) {
        v >>= 1;
        ++shift;
    }
    return (uint16_t)((original_reciprocal[v] >> shift) >> 7);
}

static void check(uint32_t v)
{
    uint16_t expected = reference(v);
    uint16_t production = (uint16_t)(FixedReciprocal((fixed_t)v) >> 7);
    uint16_t actual = NG_ColumnReciprocal(v);
    uint16_t linked = column_probe(v);
    if (production != expected) fail("production oracle", v, production, expected);
    if (actual != expected) fail("column reciprocal", v, actual, expected);
    if (linked != expected) fail("external table linkage", v, linked, expected);

    if (v >= 65536u) {
        uint32_t index = v >> 16;
        if (index >= LENGTH(ng_column_reciprocal_shift))
            fail("shift index bounds", v, index, 64);
        unsigned shift = ng_column_reciprocal_shift[index];
        uint32_t normalized = v >> shift;
        if (normalized < 32768u || normalized > 65535u)
            fail("ROM index bounds", v, normalized, 32768);
    }
}

int main(void)
{
    for (uint32_t v = 0; v < 65536u; ++v) {
        uint32_t expected = v ? UINT32_MAX / v : 0;
        uint32_t rom = v < 32768u ? reciprocalLow[v]
            : 65536u + reciprocalHigh[v - 32768u];
        if (original_reciprocal[v] != expected)
            fail("source table", v, original_reciprocal[v], expected);
        if (rom != expected) fail("ROM table", v, rom, expected);
    }

    for (unsigned i = 0; i < LENGTH(ng_column_reciprocal_shift); ++i) {
        unsigned bits = 0;
        for (unsigned n = i; n; n >>= 1) ++bits;
        if (ng_column_reciprocal_shift[i] != bits)
            fail("shift table", i, ng_column_reciprocal_shift[i], bits);
    }

    static const struct { uint32_t value; uint16_t expected; } edges[] = {
        {256, 65535}, {512, 65535}, {32767, 1024}, {32768, 1023},
        {65535, 512}, {65536, 511}, {65537, 511},
        {131071, 256}, {131072, 255}, {4194303, 8}, {4194304, 7}
    };
    for (unsigned i = 0; i < LENGTH(edges); ++i) {
        uint16_t actual = NG_ColumnReciprocal(edges[i].value);
        if (actual != edges[i].expected)
            fail("fixed boundary result", edges[i].value, actual, edges[i].expected);
    }

    for (unsigned bit = 8; bit <= 22; ++bit) {
        uint32_t power = UINT32_C(1) << bit;
        for (uint32_t v = power - 1; v <= power + 1; ++v)
            if (v >= 256u && v <= 4194304u) check(v);
    }

    uint32_t count = 0;
    for (uint32_t v = 256; v <= 4194304u; ++v) {
        check(v);
        ++count;
    }
    if (count != 4194049u) fail("exhaustive range length", 0, count, 4194049u);
    printf("PASS: %" PRIu32 " legal scales (256..4194304), 65536 ROM entries, "
           "65 shift entries, index bounds and boundary results\n", count);
    return 0;
}
'''

CONTRACT_PROBE = r'''
#include "production_column.h"
#include "production_column.h"
uint16_t column_probe(uint32_t v) { return NG_ColumnReciprocal(v); }
'''


def parse_table(source, name, count, maximum):
    matches = re.findall(
        rf"\b{re.escape(name)}\[(\d+)\][^;={{]*=\s*\{{([^}}]+)\}};", source
    )
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {name} initializer")
    declared, body = matches[0]
    tokens = body.strip().rstrip(",").split(",")
    if any(re.fullmatch(r"\s*\d+\s*", token) is None for token in tokens):
        raise ValueError(f"unexpected non-decimal initializer in {name}")
    values = [int(token) for token in tokens]
    if int(declared) != count or len(values) != count:
        raise ValueError(f"{name}: expected {count} entries, got {declared}/{len(values)}")
    if any(value > maximum for value in values):
        raise ValueError(f"{name}: initializer exceeds its element type")
    return values


def extract_function(source, name):
    matches = re.findall(
        rf"^(?:fixed_t|uint16_t)\s+(?:CONSTFUNC\s+)?{name}\([^;]*?\)\n"
        r"\{.*?^\}", source, re.MULTILINE | re.DOTALL,
    )
    if len(matches) != 1:
        raise ValueError(f"expected exactly one production {name} function")
    return matches[0]


def check_table_symbols(binary):
    result = subprocess.run(
        [*shlex.split(os.environ.get("NM", "nm")), "-S", "--defined-only",
         "--format=posix", str(binary)], check=True, text=True, capture_output=True,
    )
    for name, size in (("reciprocalLow", 131072), ("reciprocalHigh", 65536)):
        records = [line.split() for line in result.stdout.splitlines()
                   if line.split() and line.split()[0] == name]
        if len(records) != 1 or len(records[0]) != 4:
            raise ValueError(f"expected one defined {name} symbol: {records}")
        _, kind, _, actual_size = records[0]
        if kind not in ("R", "T") or int(actual_size, 16) != size:
            raise ValueError(f"expected exported read-only {name} of {size} bytes: {records}")
    print("PASS: one exported ROM object per table, 131072 + 65536 bytes (no doubling)")


def main():
    source = (ROOT / "m_fixed.c").read_text()
    rom_source = (ROOT / "neogeo/assets/generated/doom_reciprocal.h").read_text()
    header = (ROOT / "neogeo/doom_reciprocal.h").read_bytes()
    tables = (
        ("reciprocalLow", "uint32_t", parse_table(rom_source, "reciprocalLow", 32768, 0xffffffff)),
        ("reciprocalHigh", "uint16_t", parse_table(rom_source, "reciprocalHigh", 32768, 0xffff)),
        ("original_reciprocal", "uint32_t", parse_table(source, "reciprocalTable", 65536, 0xffffffff)),
    )
    functions = "\n\n".join(extract_function(source, name) for name in (
        "FixedReciprocal", "FixedReciprocalBig", "FixedReciprocalSmall",
    ))
    production = r'''
#define __NGDEVKIT__ 1
#define USE_LOOKUP_TABLE
#define CONSTFUNC __attribute__((const))
#define __builtin_clzl(v) __builtin_clz((unsigned int)(uint32_t)(v))
typedef int32_t fixed_t;
fixed_t FixedReciprocal(fixed_t v);
uint16_t FixedReciprocalBig(fixed_t v);
fixed_t FixedReciprocalSmall(uint16_t v);
''' + functions + "\n"
    print("column header sha256:", hashlib.sha256(header).hexdigest(), flush=True)
    print("ROM tables sha256:", hashlib.sha256(rom_source.encode()).hexdigest(), flush=True)
    print("production functions sha256:", hashlib.sha256(functions.encode()).hexdigest(), flush=True)

    with tempfile.TemporaryDirectory(prefix="doom-reciprocal-") as temp:
        directory = Path(temp)
        generated_path = directory / "neogeo/assets/generated/doom_reciprocal.h"
        generated_path.parent.mkdir(parents=True)
        (directory / "m_fixed.c").write_text(source, encoding="ascii")
        subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools/gen_neogeo_reciprocal.py")],
            cwd=directory, check=True,
        )
        generated = generated_path.read_text()
        for name, kind, values in tables[:2]:
            regenerated = parse_table(generated, name, 32768,
                                      0xffffffff if kind == "uint32_t" else 0xffff)
            if regenerated != values:
                raise ValueError(f"regeneration changed {name} ROM contents")
            if not re.search(rf"^const {kind} {name}\[32768\]", generated, re.MULTILINE):
                raise ValueError(f"{name} definition must have external const linkage")
        if 'reciprocalLow[32768] __attribute__((section(".text2")))' not in generated:
            raise ValueError("reciprocalLow lost its ROM bank section")
        print("PASS: regenerated definitions preserve all values, const and .text2 placement")
        (directory / "production_column.h").write_bytes(header)
        (directory / "production_fixed.h").write_text(production, encoding="ascii")
        (directory / "rom_tables.h").write_text(
            '#include <stdint.h>\n#include "neogeo/assets/generated/doom_reciprocal.h"\n'
            + "\n".join(
                f"const {kind} {name}[] = {{" + ",".join(map(str, values)) + "};"
                for name, kind, values in tables[2:]
            ) + "\n", encoding="ascii",
        )
        (directory / "verify.c").write_text(PROGRAM, encoding="ascii")
        (directory / "probe.c").write_text(CONTRACT_PROBE, encoding="ascii")
        compiler = [*shlex.split(os.environ.get("CC", "cc")), "-std=c11", "-O2",
                    "-Wall", "-Wextra", "-Werror", "-I", str(directory)]
        binary = directory / "verify"
        subprocess.run([
            *compiler, "-DCOLEXTRABITS=7", "-fsanitize=undefined,bounds",
            "-fno-sanitize-recover=all", str(directory / "verify.c"),
            str(directory / "probe.c"), "-o", str(binary),
        ], check=True)
        subprocess.run([str(binary)], check=True)
        check_table_symbols(binary)
        print("PASS: standalone header, repeated inclusion and external table linkage")

        for shift in (None, 6, 8):
            flags = [] if shift is None else [f"-DCOLEXTRABITS={shift}"]
            result = subprocess.run(
                [*compiler, *flags, "-x", "c", "-fsyntax-only", "-"],
                input=CONTRACT_PROBE, text=True, capture_output=True,
            )
            if result.returncode == 0 or "NG_ColumnReciprocal requires COLEXTRABITS == 7" not in result.stderr:
                raise RuntimeError(f"missing COLEXTRABITS rejection for {shift}: {result.stderr}")
        print("PASS: missing COLEXTRABITS and shifts 6/8 rejected")


if __name__ == "__main__":
    main()
