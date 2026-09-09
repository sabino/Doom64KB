#ifndef DOOM_NEOGEO_RECIPROCAL_H
#define DOOM_NEOGEO_RECIPROCAL_H

#include <stdint.h>

/* Include after the renderer's COLEXTRABITS definition. */
#if !defined(COLEXTRABITS) || COLEXTRABITS != 7
#error "NG_ColumnReciprocal requires COLEXTRABITS == 7 before inclusion"
#endif

/* The single ROM definitions in m_fixed.c must have external linkage. */
extern const uint32_t reciprocalLow[32768];
extern const uint16_t reciprocalHigh[32768];

static const uint8_t ng_column_reciprocal_shift[] = {
    0, 1, 2, 2, 3, 3, 3, 3,
    4, 4, 4, 4, 4, 4, 4, 4,
    5, 5, 5, 5, 5, 5, 5, 5,
    5, 5, 5, 5, 5, 5, 5, 5,
    6, 6, 6, 6, 6, 6, 6, 6,
    6, 6, 6, 6, 6, 6, 6, 6,
    6, 6, 6, 6, 6, 6, 6, 6,
    6, 6, 6, 6, 6, 6, 6, 6,
    7
};

_Static_assert(sizeof(ng_column_reciprocal_shift) == 65,
               "column normalization needs entries 0 through 64");

/* Precondition: 256 <= v <= 64 * 65536, including both endpoints.
 * Returns (uint16_t)(FixedReciprocal(v) >> COLEXTRABITS), including the
 * narrowing at scales below 512. This preserves the lookup approximation,
 * not an exact divide by v. For v >= 65536, bit_length(v >> 16) is precisely
 * FixedReciprocalBig's normalization shift, and its index is in 1..64.
 */
static inline uint16_t NG_ColumnReciprocal(uint32_t v)
{
    if (v < 32768u)
        return (uint16_t)(reciprocalLow[v] >> COLEXTRABITS);

    if (v < 65536u)
        return (uint16_t)((65536u + reciprocalHigh[v - 32768u])
                          >> COLEXTRABITS);

    const unsigned s = ng_column_reciprocal_shift[v >> 16];
    return (uint16_t)((65536u + reciprocalHigh[(v >> s) - 32768u])
                      >> (s + COLEXTRABITS));
}

#endif
