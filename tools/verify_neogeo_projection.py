#!/usr/bin/env python3
"""Check the production projection helper against a wide-product reference."""

import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    source = (ROOT / "r_draw.c").read_text()
    match = re.search(r"finetangent\[4096\]\s*=\s*\{([^}]+)\}", source)
    if match is None:
        raise ValueError("finetangent initializer not found")
    values = [int(value) for value in re.findall(r"-?\d+", match[1])]
    if len(values) != 4096:
        raise ValueError("expected 4096 tangent values")
    program = r'''
#include <inttypes.h>
#include <stdio.h>
#include "neogeo/doom_projection.h"
#include "tangents.h"

static int check(int16_t a, int32_t b)
{
    uint16_t expected = (uint32_t)((int64_t)a * b) >> 16;
    uint16_t actual = NG_ProjectionProductHigh(a, b);
    if (actual == expected)
        return 0;
    fprintf(stderr, "a=%d b=%" PRId32 " expected=%u actual=%u\n",
            a, b, expected, actual);
    return 1;
}

int main(void)
{
    const int32_t edges[] = {INT32_MIN, INT32_MAX, -65537, -65536,
                            -65535, -1, 0, 1, 65535, 65536, 65537};
    uint32_t random = 42;
    for (int32_t a = INT16_MIN; a <= INT16_MAX; ++a) {
        for (unsigned i = 0; i < 4096; ++i)
            if (check(a, tangents[i])) return 1;
        for (unsigned i = 0; i < sizeof(edges) / sizeof(edges[0]); ++i)
            if (check(a, edges[i])) return 1;
        for (unsigned i = 0; i < 32; ++i) {
            random = random * 1664525u + 1013904223u;
            if (check(a, (int32_t)random)) return 1;
        }
    }
    puts("PASS: 271253504 products (all int16 distances x all 4096 tangents, edges and random inputs)");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="doom-projection-") as temp:
        directory = Path(temp)
        (directory / "tangents.h").write_text(
            "static const int32_t tangents[4096] = {"
            + ",".join(map(str, values)) + "};\n"
        )
        (directory / "verify.c").write_text(program)
        binary = directory / "verify"
        subprocess.run([os.environ.get("CC", "cc"), "-std=c99", "-O2",
                        "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined",
                        "-I", str(ROOT), str(directory / "verify.c"),
                        "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
