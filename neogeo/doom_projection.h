#ifndef DOOM_NEOGEO_PROJECTION_H
#define DOOM_NEOGEO_PROJECTION_H

#include <stdint.h>

/* Bits 16..31 of the wrapped signed 16x32 product, including negative a. */
static inline uint16_t NG_ProjectionProductHigh(int16_t a, int32_t b)
{
    uint32_t low = (uint16_t)a;
    uint32_t high = (uint16_t)a;
#if defined __GNUC__ && defined __mc68000__
    __asm__("mulu.w %1,%0" : "+d"(low) : "d"((uint16_t)b) : "cc");
    __asm__("mulu.w %1,%0" : "+d"(high)
            : "d"((uint16_t)((uint32_t)b >> 16)) : "cc");
#else
    low *= (uint16_t)b;
    high *= (uint16_t)((uint32_t)b >> 16);
#endif
    uint16_t result = (uint16_t)((low >> 16) + high);
    if (a < 0)
        result -= (uint16_t)b;
    return result;
}

#endif
