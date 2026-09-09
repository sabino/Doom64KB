#!/usr/bin/env python3
"""Host-check production post products and the extracted masked-column routine.

Uses temporary files only. This checks C semantics, not m68k inline assembly,
real pixel callbacks, or emulator timing. The oracle models wrapped target math
with wide unsigned arithmetic rather than executing the old signed-overflow C.
"""

import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


PROGRAM = r'''
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define __NGDEVKIT__ 1
#define __far
#define FRACBITS 16
#define FRACUNIT 65536
#include "production_projection.h"
typedef uint8_t byte;
typedef int8_t height_t;
typedef int32_t fixed_t;
typedef struct { byte topdelta, length; } column_t;
typedef struct {
    int16_t x, yl, yh;
    uint16_t fracstep;
    fixed_t texturemid;
    const byte *source;
    const uint8_t *colormap;
} draw_column_vars_t;
typedef void (*R_DrawColumn_f)(const draw_column_vars_t *);
static height_t floors[3], ceilings[3];
static height_t *mfloorclip = floors, *mceilingclip = ceilings;
static fixed_t sprtopscreen, spryscale;
static int R_VIEWHEIGHT;
#include "production_masked.h"
static int16_t clipped[4][2];
static unsigned clipped_count;
static void observe_clip(int16_t yl, int16_t yh)
{
    if (clipped_count == 4) abort();
    clipped[clipped_count][0] = yl;
    clipped[clipped_count++][1] = yh;
}
#include "production_probe.h"

static uint64_t products, columns, closed_columns;
static uint32_t rng = 42;
static uint32_t next_random(void)
{
    rng = rng * UINT32_C(1664525) + UINT32_C(1013904223);
    return rng;
}

static void fail(const char *what)
{
    fprintf(stderr, "FAIL: %s (products=%" PRIu64 ", columns=%" PRIu64
            ", closed=%" PRIu64 ")\n", what, products, columns, closed_columns);
    exit(1);
}

static void product(unsigned a, uint32_t b)
{
    uint32_t expected = (uint32_t)((uint64_t)a * b);
    uint32_t actual = NG_PostProduct((uint8_t)a, b);
    ++products;
    if (actual != expected) {
        fprintf(stderr, "a=%u b=%08" PRIx32 " expected=%08" PRIx32
                " actual=%08" PRIx32 "\n", a, b, expected, actual);
        fail("post product");
    }
}

/* Explicit sign decoding avoids sharing the candidate's signed cast/shift. */
static int32_t signed_word(uint32_t bits)
{
    uint32_t word = (bits >> 16) & 65535u;
    return word < 32768u ? (int32_t)word : (int32_t)word - 65536;
}

static int equal_vars(const draw_column_vars_t *a, const draw_column_vars_t *b)
{
    return a->x == b->x && a->yl == b->yl && a->yh == b->yh
        && a->fracstep == b->fracstep && a->texturemid == b->texturemid
        && a->source == b->source && a->colormap == b->colormap;
}

static draw_column_vars_t events[4];
static unsigned event_count;
static int16_t expected_clipped[4][2];
static unsigned expected_clipped_count;
static void record(const draw_column_vars_t *vars)
{
    if (event_count == 4) fail("too many callbacks");
    events[event_count++] = *vars;
}

/* No early guard: independently walk every post and predict all callbacks. */
static unsigned reference(draw_column_vars_t *vars, const byte *post,
                          draw_column_vars_t *expected)
{
    int32_t base = vars->texturemid;
    unsigned count = 0;
    while (post[0] != 255) {
        uint32_t top = (uint32_t)((uint64_t)(uint32_t)sprtopscreen
            + (uint64_t)(uint32_t)spryscale * post[0]);
        uint32_t bottom = (uint32_t)((uint64_t)top
            + (uint64_t)(uint32_t)spryscale * post[1]);
        int32_t yl = signed_word((uint32_t)((uint64_t)top + 65535u));
        int32_t yh = signed_word((uint32_t)((uint64_t)bottom + UINT32_MAX));
        if (yh >= floors[vars->x]) yh = floors[vars->x] - 1;
        if (yl <= ceilings[vars->x]) yl = ceilings[vars->x] + 1;
        if (expected_clipped_count == 4) fail("oracle clip overflow");
        expected_clipped[expected_clipped_count][0] = (int16_t)yl;
        expected_clipped[expected_clipped_count++][1] = (int16_t)yh;
        if (yl <= yh && yh < R_VIEWHEIGHT) {
            vars->source = post + 3;
            /* Tests keep base away from overflow in this unchanged expression. */
            vars->texturemid = base - (int32_t)((uint32_t)post[0] * 65536u);
            vars->yl = (int16_t)yl;
            vars->yh = (int16_t)yh;
            if (count == 4) fail("oracle callback overflow");
            expected[count++] = *vars;
        }
        post += post[1] + 4u;
    }
    vars->texturemid = base;
    return count;
}

static const byte initial_source[1] = {17}, initial_map[1] = {93};
static draw_column_vars_t initial_vars(void)
{
    draw_column_vars_t vars = {1, -1234, 2345, 4567, 1234567,
                               initial_source, initial_map};
    return vars;
}

static size_t append_post(byte *buffer, size_t pos, unsigned top, unsigned length)
{
    buffer[pos] = (byte)top;
    buffer[pos + 1] = (byte)length;
    buffer[pos + 2] = 0;
    memset(buffer + pos + 3, 79, length);
    buffer[pos + 3 + length] = 0;
    return pos + length + 4;
}

static void column_case(const byte *buffer, uint32_t scale, uint32_t origin,
                        int floor, int ceiling, int height)
{
    draw_column_vars_t actual = initial_vars(), expected = initial_vars();
    draw_column_vars_t wanted[4];
    spryscale = (int32_t)scale;
    sprtopscreen = (int32_t)origin;
    floors[0] = -77; floors[1] = (height_t)floor; floors[2] = 88;
    ceilings[0] = -66; ceilings[1] = (height_t)ceiling; ceilings[2] = 99;
    R_VIEWHEIGHT = height;
    expected_clipped_count = 0;
    unsigned n = reference(&expected, buffer, wanted);
    event_count = 0;
    R_DrawMaskedColumn(record, &actual, (const column_t *)buffer);
    ++columns;
    if (event_count != n || !equal_vars(&actual, &expected))
        fail("callback count or final dcvars");
    for (unsigned i = 0; i < n; ++i)
        if (!equal_vars(&events[i], &wanted[i])) fail("clipped callback state/order");
    /* Observe even rejected posts in a second, minimally instrumented copy. */
    draw_column_vars_t probe = initial_vars();
    event_count = clipped_count = 0;
    R_ProbeMaskedColumn(record, &probe, (const column_t *)buffer);
    unsigned expected_count = floor <= ceiling + 1 ? 0 : expected_clipped_count;
    if (clipped_count != expected_count || event_count != n
        || !equal_vars(&probe, &expected)) fail("probe final state");
    for (unsigned i = 0; i < clipped_count; ++i)
        if (clipped[i][0] != expected_clipped[i][0]
            || clipped[i][1] != expected_clipped[i][1])
            fail("final clipped yl/yh, including rejected posts");
    if ((uint32_t)spryscale != scale || (uint32_t)sprtopscreen != origin
        || floors[0] != -77 || floors[1] != floor || floors[2] != 88
        || ceilings[0] != -66 || ceilings[1] != ceiling || ceilings[2] != 99
        || R_VIEWHEIGHT != height || mfloorclip != floors || mceilingclip != ceilings)
        fail("unexpected global state change");
}

int main(void)
{
    static const uint32_t edges[] = {
        0, 1, 2, 127, 128, 255, 256, 257, 32767, 32768, 32769,
        65534, 65535, 65536, 65537, 131071, 131072, 131073,
        0x3fffff, 0x400000, 0x400001, 0x7fffffff, 0x80000000,
        0x80000001, 0xffff0000, 0xfffffffe, 0xffffffff
    };
    static const int heights[] = {28, 37, 56};
    for (unsigned a = 0; a < 256; ++a) {
        for (unsigned i = 0; i < sizeof(edges) / sizeof(edges[0]); ++i)
            product(a, edges[i]);
        for (unsigned bit = 0; bit < 32; ++bit) {
            uint32_t v = UINT32_C(1) << bit;
            product(a, v - 1); product(a, v); product(a, v + 1);
        }
        for (unsigned h = 0; h < 3; ++h)
            for (unsigned distance = 4; distance <= 1280; ++distance)
                product(a, (uint32_t)heights[h] * 65536u / distance);
        for (unsigned i = 0; i < 4096; ++i) product(a, next_random());
    }

    byte buffer[3 * 259 + 1];
    /* Every non-sentinel topdelta and every byte length, including zero/255. */
    for (unsigned top = 0; top < 255; ++top)
        for (unsigned length = 0; length < 256; ++length) {
            size_t end = append_post(buffer, 0, top, length);
            buffer[end] = 255;
            for (unsigned i = 0; i < sizeof(edges) / sizeof(edges[0]); ++i) {
                int height = heights[i % 3];
                uint32_t origin = edges[(i + top) % (sizeof(edges) / sizeof(edges[0]))];
                column_case(buffer, edges[i], origin, height, -1, height);
            }
        }

    for (unsigned h = 0; h < 3; ++h)
        for (unsigned distance = 4; distance <= 1280; ++distance)
            for (unsigned a = 0; a < 256; ++a) {
                size_t end = append_post(buffer, 0, a % 255, a);
                buffer[end] = 255;
                uint32_t origin = ((uint32_t)(heights[h] / 2) - a) * 65536u;
                column_case(buffer, (uint32_t)heights[h] * 65536u / distance,
                            origin, heights[h], -1, heights[h]);
            }

    for (unsigned i = 0; i < 250000; ++i) {
        size_t pos = 0;
        for (unsigned p = 0; p < 3; ++p) {
            unsigned top = next_random() >> 24;
            unsigned length = next_random() >> 24;
            pos = append_post(buffer, pos, top % 255, length);
        }
        buffer[pos] = 255;
        uint32_t scale = next_random(), origin = next_random();
        int floor = (int)(next_random() >> 24) - 128;
        int ceiling = (int)(next_random() >> 24) - 128;
        column_case(buffer, scale, origin, floor, ceiling, heights[i % 3]);
    }

    /* Prove the actual guard neither reads posts nor calls a pixel callback. */
    for (unsigned h = 0; h < 3; ++h)
        for (int floor = -128; floor <= 127; ++floor)
            for (int ceiling = -128; ceiling <= 127; ++ceiling) {
                if (floor > ceiling + 1) continue;
                column_case(buffer, 65536, 0, floor, ceiling, heights[h]);
                draw_column_vars_t actual = initial_vars(), before = actual;
                event_count = 0;
                R_DrawMaskedColumn(record, &actual, NULL);
                ++closed_columns;
                if (event_count || !equal_vars(&actual, &before))
                    fail("closed interval side effects");
            }

    buffer[0] = 255;
    column_case(buffer, 65536, 0, 56, -1, 56);
    printf("PASS: %" PRIu64 " products, %" PRIu64 " extracted-function cases, "
           "%" PRIu64 " closed-guard no-read/no-callback cases\n",
           products, columns, closed_columns);
    return 0;
}
'''


def main():
    source = (ROOT / "r_draw.c").read_text()
    matches = re.findall(
        r"^static void R_DrawMaskedColumn\([^\n]+\)\n\{.*?^\}",
        source, re.MULTILINE | re.DOTALL,
    )
    if len(matches) != 1 or "NG_PostProduct" not in matches[0]:
        raise ValueError("expected one production NG masked-column function")
    header = (ROOT / "neogeo/doom_projection.h").read_bytes()
    function = matches[0].encode()
    probe = matches[0].replace("R_DrawMaskedColumn(", "R_ProbeMaskedColumn(", 1)
    marker = "        if (yl <= yh && yh < R_VIEWHEIGHT)"
    if probe.count(marker) != 1:
        raise ValueError("expected exactly one post-clip observation point")
    probe = probe.replace(marker, "        observe_clip(yl, yh);\n" + marker)
    print("projection header sha256:", hashlib.sha256(header).hexdigest(), flush=True)
    print("masked function sha256:", hashlib.sha256(function).hexdigest(), flush=True)
    with tempfile.TemporaryDirectory(prefix="doom-posts-") as temp:
        directory = Path(temp)
        (directory / "production_projection.h").write_bytes(header)
        (directory / "production_masked.h").write_bytes(function)
        (directory / "production_probe.h").write_text(probe, encoding="ascii")
        (directory / "verify.c").write_text(PROGRAM, encoding="ascii")
        binary = directory / "verify"
        subprocess.run([
            *shlex.split(os.environ.get("CC", "cc")), "-std=c99", "-O2",
            "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined",
            "-fno-sanitize-recover=undefined", "-I", str(directory),
            str(directory / "verify.c"), "-o", str(binary),
        ], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
