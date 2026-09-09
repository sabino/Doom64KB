"""Generate lossless split reciprocal tables for the two Neo Geo ROM banks."""

from pathlib import Path
import re


def main():
    source = Path("m_fixed.c").read_text()
    body = source.split("static const uint32_t reciprocalTable[65536] = {", 1)[1]
    original = [int(x) for x in re.findall(r"\d+", body.split("};", 1)[0])]
    values = [0] + [0xffffffff // v for v in range(1, 65536)]
    if original != values:
        raise ValueError("Reciprocal source table differs from the expected values")
    upper = [v - 65536 for v in values[32768:]]
    assert all(0 <= v <= 65535 for v in upper)
    assert values == values[:32768] + [v + 65536 for v in upper]
    lines = ["/* Generated lossless reciprocal tables. */"]
    for name, kind, data, attr in (
        ("reciprocalLow", "uint32_t", values[:32768], '__attribute__((section(".text2")))'),
        ("reciprocalHigh", "uint16_t", upper, ""),
    ):
        # Defined only by m_fixed.c; renderer helpers share these ROM objects.
        lines.append(f"const {kind} {name}[32768] {attr} = {{")
        lines.extend(
            ",".join(map(str, data[i:i + 16])) + ","
            for i in range(0, len(data), 16)
        )
        lines.append("};")
    Path("neogeo/assets/generated/doom_reciprocal.h").write_text("\n".join(lines) + "\n")
    print("Verified 65536 exact reciprocal entries; saved 65536 ROM bytes")


if __name__ == "__main__":
    main()
