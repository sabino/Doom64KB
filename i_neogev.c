/*-----------------------------------------------------------------------------
 *
 *
 *  Copyright (C) 2026 Frenkel Smeijers
 *
 *  This program is free software; you can redistribute it and/or
 *  modify it under the terms of the GNU General Public License
 *  as published by the Free Software Foundation; either version 2
 *  of the License, or (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program; if not, write to the Free Software
 *  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA
 *  02111-1307, USA.
 *
 * DESCRIPTION:
 *      Neo Geo video code.
 *
 *      This backend no longer uses the FIX layer as the game framebuffer.
 *      The Doom renderer uses a runtime-selected logical framebuffer size up
 *      to 80x56.  Sprite output displays that active area as 4x4, 6x6, or 8x8
 *      hardware-sprite microcells:
 *
 *          4x4: 160 sprite strips, 80 active per scanline = 320x224 pixels
 *          6x6: 106 sprite strips, 53 active per scanline = 318x222 pixels
 *          8x8:  80 sprite strips, 40 active per scanline = 320x224 pixels
 *      The full-color path uses one shrunk 16x16 C-ROM tile per logical
 *      pixel.  The hardware shrinker reduces each tile to the selected cell.
 *      Per-tile sprite attributes select repacked PLAYPAL sprite palettes,
 *      preserving the original 8-bit Doom palette colors.
 *
 *-----------------------------------------------------------------------------*/

#include <stdint.h>
#include <string.h>
#include <ngdevkit/registers.h>

#include "compiler.h"

#include "i_system.h"
#include "i_video.h"
#include "i_vtext.h"
#include "m_menu.h"
#include "m_random.h"
#include "r_defs.h"
#include "v_video.h"
#include "w_wad.h"

#include "globdata.h"

#include "neogeo/doom_fix_menu_palette.h"
#include "neogeo/doom_fix_wipe_assets.h"
#include "neogeo/doom_title_assets.h"


extern int16_t CENTERY;

/*
 * Neo Geo VRAM sprite control blocks.  ngdevkit exposes the VRAM registers;
 * using the raw SCB addresses here keeps the renderer independent from the
 * higher-level sprite helpers.
 */
/* The BIOS/ngdevkit eyecatcher uses the first C-ROM tiles. */
#define MICROFB_TILE_BASE 256u
#define MICROFB_TILE_BLANK MICROFB_TILE_BASE

/* Sprite pixels are 4bpp and pixel index 0 is transparent.  The renderer
 * therefore stores PLAYPAL colors in 15 visible slots per sprite palette and
 * uses extra sprite palettes starting at palette 16, leaving the original first
 * 16 palettes untouched for the FIX overlay.
 */
#define MICROFB_VISIBLE_COLOR_SLOTS 15u
#define MICROFB_SPRITE_PALETTE_BASE 16u
#define MICROFB_SPRITE_PALETTES ((256u + MICROFB_VISIBLE_COLOR_SLOTS - 1u) / MICROFB_VISIBLE_COLOR_SLOTS)
#define TITLE_SPRITE_PALETTE_BASE (MICROFB_SPRITE_PALETTE_BASE + MICROFB_SPRITE_PALETTES)

#define MICROFB_DISPLAY_W 320u
#define MICROFB_DISPLAY_H 224u
#define MICROFB_COLUMN_CHUNKS 2u
#define MICROFB_FRAMEBUFFER_SETS 2u
#define MICROFB_SPRITE_BASE 1u
#define MICROFB_X_WORD(x) (((uint16_t)(x)) << 7)
#define MICROFB_PALETTE_ATTR(pal) ((uint16_t)(pal) << 8)
#define MICROFB_STICKY_BIT 0x0040u

#define FIX_OVERLAY_WIDTH 38
#define FIX_OVERLAY_HEIGHT 28
#define FIX_CLEAR_CHAR ' '
#define FIX_WIPE_WIDTH 40
#define FIX_WIPE_HEIGHT 28
#define FIX_WIPE_Y_OFFSET 2
typedef enum
{
	FIX_TARGET_NONE,
	FIX_TARGET_PAGE,
	FIX_TARGET_TILED_BACKGROUND
} fix_target_kind_t;

#if (VIEWWINDOWHEIGHT % MICROFB_COLUMN_CHUNKS) != 0
#error Neo Geo sprite microframebuffer requires VIEWWINDOWHEIGHT divisible by MICROFB_COLUMN_CHUNKS
#endif

#if VIEWWINDOWWIDTH > 96
#error Neo Geo sprite microframebuffer cannot exceed 96 logical columns because of the per-scanline sprite limit
#endif

#if (VIEWWINDOWWIDTH * 4) != MICROFB_DISPLAY_W || (VIEWWINDOWHEIGHT * 4) != MICROFB_DISPLAY_H
#error Neo Geo sprite microframebuffer runtime modes assume an 80x56 source framebuffer
#endif

#define MICROFB_CHUNK_CELLS (VIEWWINDOWHEIGHT / MICROFB_COLUMN_CHUNKS)
#define MICROFB_SPRITES_PER_SET (VIEWWINDOWWIDTH * MICROFB_COLUMN_CHUNKS)
#define MICROFB_SPRITE_COUNT (MICROFB_SPRITES_PER_SET * MICROFB_FRAMEBUFFER_SETS)

#define BACKGROUND_SPRITE_BASE (MICROFB_SPRITE_BASE + MICROFB_SPRITE_COUNT)
#define BACKGROUND_SPRITE_COUNT DOOM_BACKGROUND_COLUMNS
#define BACKGROUND_X_OFFSET 8u
#define BACKGROUND_SHRINK_WORD 0x077fu
#define BACKGROUND_TILE_PX 8u

#if MICROFB_CHUNK_CELLS > 32
#error Neo Geo sprite microframebuffer chunk cannot exceed 32 source tiles
#endif

#if (MICROFB_SPRITE_BASE + MICROFB_SPRITE_COUNT) > 381
#error Neo Geo sprite microframebuffer double buffer exceeds displayable sprite budget
#endif

#if (BACKGROUND_SPRITE_BASE + BACKGROUND_SPRITE_COUNT) > 381
#error Neo Geo static background sprites exceed displayable sprite budget
#endif

#if (MICROFB_SPRITE_PALETTE_BASE + MICROFB_SPRITE_PALETTES) > 256
#error Neo Geo sprite microframebuffer exceeds sprite palette budget
#endif

#if (TITLE_SPRITE_PALETTE_BASE + DOOM_TITLE_PALETTE_COUNT) > 256
#error Neo Geo TITLEPIC exceeds sprite palette budget
#endif

#if (TITLE_SPRITE_PALETTE_BASE + DOOM_WIMAP_PALETTE_COUNT) > 256
#error Neo Geo WIMAP0 exceeds sprite palette budget
#endif

#if (TITLE_SPRITE_PALETTE_BASE + DOOM_HELP_PALETTE_COUNT) > 256
#error Neo Geo HELP2 exceeds sprite palette budget
#endif

typedef enum
{
	STATIC_BACKGROUND_NONE,
	STATIC_BACKGROUND_TITLE,
	STATIC_BACKGROUND_WIMAP,
	STATIC_BACKGROUND_HELP2
} static_background_t;

typedef struct
{
	const char *name;
	uint8_t cell_px;
	uint8_t cols;
	uint8_t rows;
	uint16_t shrink_word;
	uint16_t x_offset;
	uint16_t y_offset;
} microfb_mode_t;

static const microfb_mode_t microfb_modes[] =
{
	{ "Low", 8, 40, 28, 0x077fu, 0, 0 },
	{ "Medium", 6, 53, 37, 0x055fu, 1, 1 },
	{ "High", 4, 80, 56, 0x033fu, 0, 0 }
};

#define MICROFB_MODE_COUNT (sizeof(microfb_modes) / sizeof(microfb_modes[0]))
#define MICROFB_DEFAULT_MODE_INDEX (MICROFB_MODE_COUNT - 1u)


static uint8_t _s_screen[VIEWWINDOWWIDTH * VIEWWINDOWHEIGHT];
#define MICROFB_COLUMN(x) (&_s_screen[(uint16_t)(x) * VIEWWINDOWHEIGHT])
#define MICROFB_PIXEL(x, y) (MICROFB_COLUMN(x)[(uint16_t)(y)])
/* Packed SCB1 words: palette attribute in the high byte, tile slot in the low. */
static uint16_t _s_color_to_scb1[256];
_Static_assert(sizeof(_s_color_to_scb1) == 512u, "Neo Geo color map must stay 512 bytes");
_Static_assert((MICROFB_TILE_BASE & 0x00ffu) == 0u,
	"packed SCB1 upload requires a tile-page-aligned base");
static volatile uint8_t _s_visible_sprite_set;
static volatile uint8_t _s_swap_pending;
static volatile uint8_t _s_swap_target_set;
static volatile uint8_t _s_main_vram_write_depth;
static uint8_t _s_fix_page_active;
static uint8_t _s_fix_wipe_active;
static fix_target_kind_t _s_fix_target_kind;
static const uint16_t *_s_fix_target;
static uint8_t _s_microfb_mode_index;
static uint8_t _s_pending_microfb_mode_index;
static uint8_t _s_configured_microfb_mode[2];
static static_background_t _s_static_background_configured;
static static_background_t _s_static_background_active;
static static_background_t _s_static_background_requested;
static const uint16_t *_s_static_background_wipe_source;
static uint8_t _s_fix_menu_palette_requested;
static uint8_t _s_fix_menu_palette_applied;
static uint8_t _s_fix_wipe_restore_menu_palette;
static int8_t _s_current_palette;

static int16_t palettelumpnum;
static int8_t newpal = 100;

#define NO_PALETTE_CHANGE 100


static uint16_t NG_SpriteYWord(uint16_t y, uint16_t height_tiles)
{
	return ((((uint16_t)(496u - y)) & 0x01ffu) << 7) | (height_tiles & 0x003fu);
}


static uint8_t NG_InVBlank(void)
{
	return (*REG_LSPCMODE & 0x8000u) == 0;
}


static uint8_t NG_WaitVBlankStart(void)
{
	const uint8_t overrun = NG_InVBlank();
	while (NG_InVBlank())
	{
	}
	while (!NG_InVBlank())
	{
	}
	return overrun;
}


static void NG_BeginMainVramWrite(void)
{
	_s_main_vram_write_depth++;
	__asm__ volatile ("" ::: "memory");
}


static void NG_EndMainVramWrite(void)
{
	__asm__ volatile ("" ::: "memory");
	_s_main_vram_write_depth--;
}


static void NG_WaitPendingSpriteSwap(void)
{
	while (_s_swap_pending)
	{
	}
}


static uint16_t NG_MicroSpriteIndex(uint16_t set, uint16_t chunk, uint16_t x)
{
	return MICROFB_SPRITE_BASE + set * MICROFB_SPRITES_PER_SET + chunk * VIEWWINDOWWIDTH + x;
}


static const microfb_mode_t *NG_MicroFramebufferMode(void)
{
	return &microfb_modes[_s_microfb_mode_index];
}


static void NG_RescaleMicroFramebuffer(const microfb_mode_t *old_mode, const microfb_mode_t *new_mode)
{
	if (new_mode->cols >= old_mode->cols)
	{
		for (int16_t x = new_mode->cols - 1; x >= 0; x--)
		{
			const uint16_t src_x = ((uint16_t)x * old_mode->cols) / new_mode->cols;
			for (int16_t y = new_mode->rows - 1; y >= 0; y--)
			{
				const uint16_t src_y = ((uint16_t)y * old_mode->rows) / new_mode->rows;
				MICROFB_PIXEL(x, y) = MICROFB_PIXEL(src_x, src_y);
			}
		}
	}
	else
	{
		for (uint16_t x = 0; x < new_mode->cols; x++)
		{
			const uint16_t src_x = (x * old_mode->cols) / new_mode->cols;
			for (uint16_t y = 0; y < new_mode->rows; y++)
			{
				const uint16_t src_y = (y * old_mode->rows) / new_mode->rows;
				MICROFB_PIXEL(x, y) = MICROFB_PIXEL(src_x, src_y);
			}
		}
	}
}


static void NG_ApplyMicroFramebufferMode(uint8_t mode_index)
{
	if (mode_index != _s_microfb_mode_index)
	{
		const microfb_mode_t *previous_mode = &microfb_modes[_s_microfb_mode_index];

		_s_microfb_mode_index = mode_index;

		const microfb_mode_t *mode = NG_MicroFramebufferMode();
		NG_RescaleMicroFramebuffer(previous_mode, mode);
		R_SetRenderSize(mode->cols, mode->rows);
	}
}


static uint16_t NG_MicroFramebufferChunkRows(const microfb_mode_t *mode, uint16_t chunk)
{
	const uint16_t row_base = chunk * MICROFB_CHUNK_CELLS;

	if (row_base >= mode->rows)
		return 0;

	const uint16_t rows_left = mode->rows - row_base;
	return rows_left > MICROFB_CHUNK_CELLS ? MICROFB_CHUNK_CELLS : rows_left;
}


static void NG_BuildSpritePalettes(const uint16_t *src)
{
	for (uint16_t pal = 0; pal < MICROFB_SPRITE_PALETTES; pal++)
	{
		const uint16_t hwpal = MICROFB_SPRITE_PALETTE_BASE + pal;
		uint16_t *dst = (uint16_t*)&MMAP_PALBANK1[hwpal * 16u];

		dst[0] = 0x8000; /* sprite pixel 0 is transparent; keep it black. */

		for (uint16_t slot = 1; slot <= MICROFB_VISIBLE_COLOR_SLOTS; slot++)
		{
			const uint16_t color = pal * MICROFB_VISIBLE_COLOR_SLOTS + (slot - 1u);
			dst[slot] = color < 256u ? src[color] : 0x8000;
		}
	}

}


static void NG_UploadStaticBackgroundPalettes(static_background_t background)
{
	const uint16_t *src;
	uint16_t palette_count;

	if (background == STATIC_BACKGROUND_WIMAP)
	{
		src = &doom_wimap_palettes[0][0];
		palette_count = DOOM_WIMAP_PALETTE_COUNT;
	}
	else if (background == STATIC_BACKGROUND_HELP2)
	{
		src = &doom_help_palettes[0][0];
		palette_count = DOOM_HELP_PALETTE_COUNT;
	}
	else
	{
		src = &doom_title_palettes[0][0];
		palette_count = DOOM_TITLE_PALETTE_COUNT;
	}

	volatile uint16_t *dst = &MMAP_PALBANK1[TITLE_SPRITE_PALETTE_BASE * 16u];
	for (uint16_t i = 0; i < palette_count * 16u; i++)
		dst[i] = src[i];
}


static void NG_UploadFixPalette(const uint16_t *src)
{
	volatile uint16_t *dst = &MMAP_PALBANK1[0];

	/* Palette RAM requires word writes; memcpy emits unsupported long writes. */
	for (uint16_t i = 0; i < 256u; i++)
		dst[i] = src[i];
}


static void NG_InitColorTileMap(void)
{
	uint16_t color = 0;
	for (uint16_t palette = 0; palette < MICROFB_SPRITE_PALETTES; palette++)
		for (uint16_t slot = 1; slot <= MICROFB_VISIBLE_COLOR_SLOTS && color < 256u; slot++, color++)
		{
			_s_color_to_scb1[color] =
				MICROFB_PALETTE_ATTR(MICROFB_SPRITE_PALETTE_BASE + palette) | slot;
		}
}


void I_ReloadPalette(void)
{
	char lumpName[8] = "PLAYPAL";
	if (_g_gamma != 0)
	{
		lumpName[7] = '0' + _g_gamma;
	}

	palettelumpnum = W_GetNumForName(lumpName);
}


static void I_UploadNewPalette(int8_t pal)
{
	const uint16_t *palette_lump = W_GetLumpByNum(palettelumpnum);
	const uint16_t *src = &palette_lump[256 * pal];
	_s_current_palette = pal;

	MMAP_PALBANK1[0xfff] = src[0];
	if (!_s_fix_menu_palette_requested)
	{
		NG_UploadFixPalette(src);
		_s_fix_menu_palette_applied = false;
	}

	NG_BuildSpritePalettes(src);
}


static void NG_ApplyPendingPalettes(void)
{
	if (newpal != NO_PALETTE_CHANGE)
	{
		I_UploadNewPalette(newpal);
		newpal = NO_PALETTE_CHANGE;
	}

	if (_s_fix_menu_palette_requested != _s_fix_menu_palette_applied)
	{
		if (_s_fix_menu_palette_requested)
			NG_UploadFixPalette(&doom_menu_palettes[0][0]);
		else
		{
			const uint16_t *palette_lump = W_GetLumpByNum(palettelumpnum);
			NG_UploadFixPalette(&palette_lump[256 * _s_current_palette]);
		}
		_s_fix_menu_palette_applied = _s_fix_menu_palette_requested;
	}
}


static void NG_ClearFixOverlay(void)
{
	*REG_VRAMMOD = 32;
	for (uint16_t y = 0; y < 32; y++)
	{
		*REG_VRAMADDR = ADDR_FIXMAP + y;
		for (uint16_t x = 0; x < 40; x++)
			*REG_VRAMRW = FIX_CLEAR_CHAR;
	}
	_s_fix_page_active = false;
}


static uint16_t NG_FixTargetEntry(uint16_t x, uint16_t y)
{
	if (_s_fix_target_kind == FIX_TARGET_TILED_BACKGROUND)
		return _s_fix_target[(y & 7u) * 8u + (x & 7u)];

	return _s_fix_target[y * FIX_OVERLAY_WIDTH + x];
}


static void NG_UploadFixTarget(void)
{
	if (!_s_fix_target)
		return;

	if (!_s_fix_page_active)
		NG_ClearFixOverlay();

	*REG_VRAMMOD = 32;
	for (uint16_t y = 0; y < FIX_OVERLAY_HEIGHT; y++)
	{
		*REG_VRAMADDR = ADDR_FIXMAP + 32u + 2u + y;
		for (uint16_t x = 0; x < FIX_OVERLAY_WIDTH; x++)
			*REG_VRAMRW = NG_FixTargetEntry(x, y);
	}

	_s_fix_page_active = true;
}


static void NG_SetFixTarget(const uint16_t *target, fix_target_kind_t kind)
{
	_s_fix_target = target;
	_s_fix_target_kind = kind;

	if (!_s_fix_wipe_active)
		NG_UploadFixTarget();
}


static void NG_UploadFixOverlay(void)
{
	/* FIX text is written directly to VRAM to keep the sprite backend out of
	 * the Neo Geo's tight 64 KB work RAM.
	 */
}


static void NG_ClearSpriteState(void)
{
	/* Hide the displayable sprite range in case the BIOS/eyecatcher left sprite
	 * data behind.  Size 0 is enough to remove the strip from the display list.
	 */
	*REG_VRAMMOD = 0x200;
	for (uint16_t i = 0; i < 381; i++)
	{
		*REG_VRAMADDR = ADDR_SCB2 + i;
		*REG_VRAMRW = 0x0000;
		*REG_VRAMRW = 0x0000;
		*REG_VRAMRW = 0x0000;
	}
}


static void NG_ConfigureMicroSpriteSet(uint16_t set)
{
	const microfb_mode_t *mode = NG_MicroFramebufferMode();

	*REG_VRAMMOD = 0x200;
	for (uint16_t chunk = 0; chunk < MICROFB_COLUMN_CHUNKS; chunk++)
	{
		const uint16_t chunk_rows = NG_MicroFramebufferChunkRows(mode, chunk);

		for (uint16_t x = 0; x < VIEWWINDOWWIDTH; x++)
		{
			const uint16_t sprite = NG_MicroSpriteIndex(set, chunk, x);
			const uint8_t active = x < mode->cols && chunk_rows != 0u;

			*REG_VRAMADDR = ADDR_SCB2 + sprite;
			*REG_VRAMRW = mode->shrink_word;
			*REG_VRAMRW = active && x ? MICROFB_STICKY_BIT : 0u;
			*REG_VRAMRW = active ? MICROFB_X_WORD(mode->x_offset + x * mode->cell_px) : 0u;
		}
	}
	_s_configured_microfb_mode[set] = _s_microfb_mode_index;
}


static void NG_SetMicroSpriteSetVisible(uint16_t set, uint8_t visible)
{
	const microfb_mode_t *mode = NG_MicroFramebufferMode();

	*REG_VRAMMOD = 1;
	for (uint16_t chunk = 0; chunk < MICROFB_COLUMN_CHUNKS; chunk++)
	{
		const uint16_t row_base = chunk * MICROFB_CHUNK_CELLS;
		const uint16_t chunk_rows = NG_MicroFramebufferChunkRows(mode, chunk);
		const uint16_t y = mode->y_offset + row_base * mode->cell_px;
		const uint16_t height_word = visible && chunk_rows ? NG_SpriteYWord(y, chunk_rows) : 0u;

		*REG_VRAMADDR = ADDR_SCB3 + NG_MicroSpriteIndex(set, chunk, 0);
		*REG_VRAMRW = height_word;
	}
}


void I_NeoGeoVBlank(void)
{
	if (!_s_swap_pending || _s_main_vram_write_depth)
		return;

	const uint8_t next_sprite_set = _s_swap_target_set;
	NG_SetMicroSpriteSetVisible(_s_visible_sprite_set, false);
	NG_SetMicroSpriteSetVisible(next_sprite_set, true);
	_s_visible_sprite_set = next_sprite_set;
	__asm__ volatile ("" ::: "memory");
	_s_swap_pending = false;
}


static void NG_InitMicroSprites(void)
{
	NG_ClearSpriteState();

	/* Two complete sprite-framebuffer sets are reserved.  Only one set is visible
	 * while the next frame is uploaded into the hidden set.
	 */
	*REG_VRAMMOD = 1;
	for (uint16_t s = 0; s < MICROFB_SPRITE_COUNT; s++)
	{
		const uint16_t sprite = MICROFB_SPRITE_BASE + s;
		*REG_VRAMADDR = ADDR_SCB1 + (sprite * 64u);
		for (uint16_t t = 0; t < 32; t++)
		{
			*REG_VRAMRW = MICROFB_TILE_BLANK;
			*REG_VRAMRW = 0x0000;
		}
	}

	_s_visible_sprite_set = 0;
	NG_ConfigureMicroSpriteSet(0);
	NG_ConfigureMicroSpriteSet(1);
	NG_SetMicroSpriteSetVisible(0, true);
	NG_SetMicroSpriteSetVisible(1, false);
}


static void NG_ConfigureStaticBackgroundSprites(static_background_t background)
{
	uint16_t tile_base = DOOM_TITLE_TILE_BASE;
	const uint8_t *palette_map = doom_title_palette_map;
	if (background == STATIC_BACKGROUND_WIMAP)
	{
		tile_base = DOOM_WIMAP_TILE_BASE;
		palette_map = doom_wimap_palette_map;
	}
	else if (background == STATIC_BACKGROUND_HELP2)
	{
		tile_base = DOOM_HELP_TILE_BASE;
		palette_map = doom_help_palette_map;
	}

	*REG_VRAMMOD = 1;
	for (uint16_t col = 0; col < DOOM_BACKGROUND_COLUMNS; col++)
	{
		const uint16_t sprite = BACKGROUND_SPRITE_BASE + col;
		*REG_VRAMADDR = ADDR_SCB1 + (sprite * 64u);
		for (uint16_t row = 0; row < DOOM_BACKGROUND_ROWS; row++)
		{
			const uint16_t palette = palette_map[row * DOOM_BACKGROUND_COLUMNS + col];
			*REG_VRAMRW = tile_base + row * DOOM_BACKGROUND_COLUMNS + col;
			*REG_VRAMRW = MICROFB_PALETTE_ATTR(
				TITLE_SPRITE_PALETTE_BASE + palette);
		}
		for (uint16_t row = DOOM_BACKGROUND_ROWS; row < 32; row++)
		{
			*REG_VRAMRW = MICROFB_TILE_BLANK;
			*REG_VRAMRW = 0;
		}

		*REG_VRAMMOD = 0x200;
		*REG_VRAMADDR = ADDR_SCB2 + sprite;
		*REG_VRAMRW = BACKGROUND_SHRINK_WORD;
		*REG_VRAMRW = 0;
		*REG_VRAMRW = MICROFB_X_WORD(BACKGROUND_X_OFFSET + col * BACKGROUND_TILE_PX);
		*REG_VRAMMOD = 1;
	}

	_s_static_background_configured = background;
}


static void NG_SetStaticBackgroundSpritesVisible(uint8_t visible)
{
	const uint16_t height_word = visible ? NG_SpriteYWord(0, DOOM_BACKGROUND_ROWS) : 0;
	*REG_VRAMMOD = 1;
	*REG_VRAMADDR = ADDR_SCB3 + BACKGROUND_SPRITE_BASE;
	for (uint16_t col = 0; col < BACKGROUND_SPRITE_COUNT; col++)
		*REG_VRAMRW = height_word;
}


void I_InitGraphicsHardwareSpecificCode(void)
{
	_s_swap_pending = false;
	_s_main_vram_write_depth = 0;
	NG_BeginMainVramWrite();
	*REG_VRAMMOD = 32;
	MMAP_PALBANK1[0] = 0x8000;

	I_ReloadPalette();
	NG_InitColorTileMap();
	_s_fix_menu_palette_requested = false;
	_s_fix_menu_palette_applied = false;
	I_UploadNewPalette(0);

	_s_microfb_mode_index = MICROFB_DEFAULT_MODE_INDEX;
	_s_pending_microfb_mode_index = _s_microfb_mode_index;
	R_SetRenderSize(microfb_modes[MICROFB_DEFAULT_MODE_INDEX].cols, microfb_modes[MICROFB_DEFAULT_MODE_INDEX].rows);
	memset(_s_screen, 0, sizeof(_s_screen));
	NG_ClearFixOverlay();
	NG_InitMicroSprites();
	_s_static_background_configured = STATIC_BACKGROUND_NONE;
	_s_static_background_active = STATIC_BACKGROUND_NONE;
	_s_static_background_requested = STATIC_BACKGROUND_NONE;
	_s_static_background_wipe_source = NULL;
	NG_UploadFixOverlay();
	NG_EndMainVramWrite();
}


void I_ShutdownGraphics(void)
{
	// Do nothing
}


void I_SetPalette(int8_t p)
{
	newpal = p;
}


void V_SetSTPalette(void)
{
	// Do nothing
}


#define MICROFB_UPLOAD_CELL_ASM \
	"moveq #0,%%d2\n\t" \
	"move.b %[src]@+,%%d2\n\t" \
	"add.w %%d2,%%d2\n\t" \
	"move.w %[map]@(0,%%d2:w),%%d1\n\t" \
	"move.b %%d1,%%d0\n\t" \
	"move.w %%d0,%[vramrw]@\n\t" \
	"clr.b %%d1\n\t" \
	"move.w %%d1,%[vramrw]@\n\t"


static void NG_UploadMicroFramebuffer(uint8_t set)
{
	const microfb_mode_t *mode = NG_MicroFramebufferMode();

	*REG_VRAMMOD = 1;
	for (uint16_t chunk = 0; chunk < MICROFB_COLUMN_CHUNKS; chunk++)
	{
		const uint16_t row_base = chunk * MICROFB_CHUNK_CELLS;
		const uint16_t chunk_rows = NG_MicroFramebufferChunkRows(mode, chunk);

		if (!chunk_rows)
			continue;

#if MICROFB_CHUNK_CELLS == 28
		if (chunk_rows == MICROFB_CHUNK_CELLS)
		{
			for (uint16_t x = 0; x < mode->cols; x++)
			{
				const uint16_t sprite = NG_MicroSpriteIndex(set, chunk, x);
				const uint8_t *src = MICROFB_COLUMN(x) + row_base;

				*REG_VRAMADDR = ADDR_SCB1 + (sprite * 64u);
				__asm__ volatile (
					"move.w %[tile_base],%%d0\n\t"
					".rept 28\n\t"
					MICROFB_UPLOAD_CELL_ASM
					".endr"
					: [src] "+&a" (src)
					: [map] "a" (_s_color_to_scb1), [vramrw] "a" (REG_VRAMRW),
					  [tile_base] "i" (MICROFB_TILE_BASE)
					: "d0", "d1", "d2", "cc", "memory"
				);
			}
		}
		else
#endif
		{
			for (uint16_t x = 0; x < mode->cols; x++)
			{
				const uint16_t sprite = NG_MicroSpriteIndex(set, chunk, x);
				const uint8_t *src = MICROFB_COLUMN(x) + row_base;

				*REG_VRAMADDR = ADDR_SCB1 + (sprite * 64u);
				uint16_t rows = chunk_rows - 1u;
				__asm__ volatile (
					"move.w %[tile_base],%%d0\n\t"
					"1:\n\t"
					MICROFB_UPLOAD_CELL_ASM
					"dbra %[rows],1b"
					: [src] "+&a" (src), [rows] "+&d" (rows)
					: [map] "a" (_s_color_to_scb1), [vramrw] "a" (REG_VRAMRW),
					  [tile_base] "i" (MICROFB_TILE_BASE)
					: "d0", "d1", "d2", "cc", "memory"
				);
			}
		}
	}
}


#undef MICROFB_UPLOAD_CELL_ASM


void I_NeoGeoChangeSpriteQuality(int16_t direction)
{
	if (direction < 0)
	{
		if (_s_pending_microfb_mode_index)
			_s_pending_microfb_mode_index--;
	}
	else if (_s_pending_microfb_mode_index + 1u < MICROFB_MODE_COUNT)
	{
		_s_pending_microfb_mode_index++;
	}
}


const char *I_NeoGeoSpriteQualityName(void)
{
	return microfb_modes[_s_pending_microfb_mode_index].name;
}


void I_FinishUpdate(void)
{
	/*
	 * A fast timedemo can reach the next frame before VBlank.  Drain that
	 * request before reusing its now-visible set as the next upload target.
	 */
	NG_WaitPendingSpriteSwap();
	NG_BeginMainVramWrite();

	if (_s_static_background_requested != STATIC_BACKGROUND_NONE)
	{
		NG_WaitVBlankStart();
		NG_ApplyPendingPalettes();
		if (_s_static_background_active == STATIC_BACKGROUND_NONE)
		{
			NG_SetMicroSpriteSetVisible(_s_visible_sprite_set, false);
		}
		else if (_s_static_background_active != _s_static_background_requested)
			NG_SetStaticBackgroundSpritesVisible(false);

		if (_s_static_background_configured != _s_static_background_requested)
		{
			NG_UploadStaticBackgroundPalettes(_s_static_background_requested);
			NG_ConfigureStaticBackgroundSprites(_s_static_background_requested);
		}
		NG_SetStaticBackgroundSpritesVisible(true);
		_s_static_background_active = _s_static_background_requested;
		NG_UploadFixOverlay();
		_s_static_background_requested = STATIC_BACKGROUND_NONE;
		NG_ApplyMicroFramebufferMode(_s_pending_microfb_mode_index);
		NG_EndMainVramWrite();
		return;
	}

	const uint8_t next_sprite_set = _s_visible_sprite_set ^ 1u;
	NG_UploadMicroFramebuffer(next_sprite_set);
	if (_s_configured_microfb_mode[next_sprite_set] != _s_microfb_mode_index)
		NG_ConfigureMicroSpriteSet(next_sprite_set);

	const uint8_t synchronous =
		_s_fix_wipe_active
		|| _s_static_background_active != STATIC_BACKGROUND_NONE
		|| newpal != NO_PALETTE_CHANGE
		|| _s_fix_menu_palette_requested != _s_fix_menu_palette_applied
		|| _s_pending_microfb_mode_index != _s_microfb_mode_index;

	if (synchronous)
	{
		NG_WaitVBlankStart();
		NG_ApplyPendingPalettes();
		if (_s_static_background_active != STATIC_BACKGROUND_NONE)
		{
			NG_SetStaticBackgroundSpritesVisible(false);
			_s_static_background_active = STATIC_BACKGROUND_NONE;
		}
		else
		{
			NG_SetMicroSpriteSetVisible(_s_visible_sprite_set, false);
		}
		NG_SetMicroSpriteSetVisible(next_sprite_set, true);
		_s_visible_sprite_set = next_sprite_set;
		NG_UploadFixOverlay();
		NG_ApplyMicroFramebufferMode(_s_pending_microfb_mode_index);
		NG_EndMainVramWrite();
		return;
	}

	NG_UploadFixOverlay();

	/*
	 * Publish only after every main-thread VRAM access is complete.  The
	 * VBlank callback owns the two SCB3 visibility writes for this request.
	 */
	_s_swap_target_set = next_sprite_set;
	NG_EndMainVramWrite();
	__asm__ volatile ("" ::: "memory");
	_s_swap_pending = true;
}


void I_NeoGeoSetFixMenuPalette(boolean active)
{
	if (_s_fix_wipe_active)
		_s_fix_wipe_restore_menu_palette = active;
	else
		_s_fix_menu_palette_requested = active;
}


boolean I_NeoGeoFixWipeActive(void)
{
	return _s_fix_wipe_active;
}


#define COLEXTRABITS (8 - 1)
#define COLBITS (8 + 1)

#define DRAW_WALL_PIXEL_ASM \
	"move.b %[frac_hi],%[texel]\n\t" \
	"lsr.b #1,%[texel]\n\t" \
	"move.b (%[source],%[texel].w),%[texel]\n\t" \
	"move.b (%[colormap],%[texel].w),(%[dest])+\n\t" \
	"add.b %[step_lo],%[frac_lo]\n\t" \
	"addx.b %[step_hi],%[frac_hi]\n\t"


void R_DrawColumnSprite(const draw_column_vars_t *dcvars)
{
#if defined __GNUC__ && defined __mc68000__
	R_DrawColumnWall(dcvars);
#else
	int16_t count = (dcvars->yh - dcvars->yl) + 1;

	if (count <= 0)
		return;

	const uint8_t *source = dcvars->source;
	const uint8_t *colormap = dcvars->colormap;
	uint8_t *dest = MICROFB_COLUMN(dcvars->x) + dcvars->yl;

	const uint16_t fracstep = dcvars->fracstep;
	uint16_t frac = (dcvars->texturemid >> COLEXTRABITS) + (dcvars->yl - CENTERY) * fracstep;

	while (count--)
	{
		*dest = colormap[source[frac >> COLBITS]];
		dest++;
		frac += fracstep;
	}
#endif
}


#if defined __GNUC__ && defined __mc68000__
__attribute__((noipa, hot))
#endif
void R_DrawColumnWall(const draw_column_vars_t *dcvars)
{
#if defined __GNUC__ && defined __mc68000__
	int16_t count = (dcvars->yh - dcvars->yl) + 1;

	if (count <= 0)
		return;

	const uint8_t *source = dcvars->source;
	const uint8_t *colormap = dcvars->colormap;
	uint8_t *dest = MICROFB_COLUMN(dcvars->x) + dcvars->yl;

	const uint16_t fracstep = dcvars->fracstep;
	const uint16_t frac = (dcvars->texturemid >> COLEXTRABITS)
		+ (dcvars->yl - CENTERY) * fracstep;
	uint16_t frac_lo = (uint8_t)frac;
	uint16_t frac_hi = frac >> 8;
	const uint16_t step_lo = (uint8_t)fracstep;
	const uint16_t step_hi = fracstep >> 8;
	uint16_t texel;

	if (count < 8)
	{
		uint16_t short_iterations = (uint16_t)count - 1u;
		__asm__ volatile (
			"moveq #0,%[texel]\n\t"
			"1:\n\t"
			DRAW_WALL_PIXEL_ASM
			"dbra %[iterations],1b"
			: [dest] "+&a" (dest),
			  [frac_lo] "+&d" (frac_lo),
			  [frac_hi] "+&d" (frac_hi),
			  [iterations] "+&d" (short_iterations),
			  [texel] "=&d" (texel)
			: [source] "a" (source),
			  [colormap] "a" (colormap),
			  [step_lo] "d" (step_lo),
			  [step_hi] "d" (step_hi)
			: "cc", "memory"
		);
		return;
	}

	uint16_t iterations = (uint16_t)count - 4u;

	/*
	 * Keep the 16-bit phase in two low register bytes. On a 68000 this
	 * replaces the per-pixel variable shift and long masks with byte
	 * arithmetic; ADDX carries the low-byte phase into the high byte. Four
	 * pixels share the hot loop branch, then the biased counter handles the
	 * zero-to-three-pixel tail without another quotient or remainder.
	 */
	__asm__ volatile (
		"moveq #0,%[texel]\n\t"
		"1:\n\t"
		DRAW_WALL_PIXEL_ASM
		DRAW_WALL_PIXEL_ASM
		DRAW_WALL_PIXEL_ASM
		DRAW_WALL_PIXEL_ASM
		"subq.w #4,%[iterations]\n\t"
		"bpl.s 1b\n\t"
		"2:\n\t"
		"addq.w #3,%[iterations]\n\t"
		"bmi.s 4f\n\t"
		"3:\n\t"
		DRAW_WALL_PIXEL_ASM
		"dbra %[iterations],3b\n\t"
		"4:"
		: [dest] "+&a" (dest),
		  [frac_lo] "+&d" (frac_lo),
		  [frac_hi] "+&d" (frac_hi),
		  [iterations] "+&d" (iterations),
		  [texel] "=&d" (texel)
		: [source] "a" (source),
		  [colormap] "a" (colormap),
		  [step_lo] "d" (step_lo),
		  [step_hi] "d" (step_hi)
		: "cc", "memory"
	);
#else
	R_DrawColumnSprite(dcvars);
#endif
}

#undef DRAW_WALL_PIXEL_ASM


void R_DrawColumnFlat(uint8_t color, const draw_column_vars_t *dcvars)
{
	int16_t count = (dcvars->yh - dcvars->yl) + 1;

	if (count <= 0)
		return;

	uint8_t *dest = MICROFB_COLUMN(dcvars->x) + dcvars->yl;
#if defined __GNUC__ && defined __mc68000__
	if (count < 8)
	{
		uint16_t short_iterations = (uint16_t)count - 1u;
		__asm__ volatile (
			"1:\n\t"
			"move.b %[color],(%[dest])+\n\t"
			"dbra %[iterations],1b"
			: [dest] "+&a" (dest),
			  [iterations] "+&d" (short_iterations)
			: [color] "d" (color)
			: "cc", "memory"
		);
		return;
	}

	uint16_t iterations = (uint16_t)count - 4u;
	__asm__ volatile (
		"1:\n\t"
		"move.b %[color],(%[dest])+\n\t"
		"move.b %[color],(%[dest])+\n\t"
		"move.b %[color],(%[dest])+\n\t"
		"move.b %[color],(%[dest])+\n\t"
		"subq.w #4,%[iterations]\n\t"
		"bpl.s 1b\n\t"
		"addq.w #3,%[iterations]\n\t"
		"bmi.s 3f\n\t"
		"2:\n\t"
		"move.b %[color],(%[dest])+\n\t"
		"dbra %[iterations],2b\n\t"
		"3:"
		: [dest] "+&a" (dest),
		  [iterations] "+&d" (iterations)
		: [color] "d" (color)
		: "cc", "memory"
	);
#else
	while (count--)
		*dest++ = color;
#endif
}


#define FUZZCOLOR1 34
#define FUZZCOLOR2 148
#define FUZZCOLOR3 212
#define FUZZCOLOR4 246
#define FUZZTABLE 50

static const uint8_t fuzzcolors[FUZZTABLE] =
{
	FUZZCOLOR1,FUZZCOLOR2,FUZZCOLOR3,FUZZCOLOR4,FUZZCOLOR1,FUZZCOLOR3,FUZZCOLOR2,
	FUZZCOLOR1,FUZZCOLOR3,FUZZCOLOR4,FUZZCOLOR1,FUZZCOLOR3,FUZZCOLOR1,FUZZCOLOR2,
	FUZZCOLOR3,FUZZCOLOR1,FUZZCOLOR3,FUZZCOLOR4,FUZZCOLOR2,FUZZCOLOR4,FUZZCOLOR2,
	FUZZCOLOR1,FUZZCOLOR4,FUZZCOLOR2,FUZZCOLOR3,FUZZCOLOR1,FUZZCOLOR3,FUZZCOLOR1,FUZZCOLOR4,
	FUZZCOLOR3,FUZZCOLOR2,FUZZCOLOR1,FUZZCOLOR3,FUZZCOLOR4,FUZZCOLOR2,FUZZCOLOR1,
	FUZZCOLOR3,FUZZCOLOR4,FUZZCOLOR2,FUZZCOLOR4,FUZZCOLOR2,FUZZCOLOR1,FUZZCOLOR3,
	FUZZCOLOR1,FUZZCOLOR3,FUZZCOLOR4,FUZZCOLOR1,FUZZCOLOR3,FUZZCOLOR2,FUZZCOLOR1
};


void R_DrawFuzzColumn(const draw_column_vars_t *dcvars)
{
	int16_t count = (dcvars->yh - dcvars->yl) + 1;

	if (count <= 0)
		return;

	uint8_t *dest = MICROFB_COLUMN(dcvars->x) + dcvars->yl;

	static int16_t fuzzpos = 0;

	do
	{
		*dest = fuzzcolors[fuzzpos];
		dest++;

		fuzzpos++;
		if (fuzzpos >= FUZZTABLE)
			fuzzpos = 0;

	} while (--count);
}


void V_ClearViewWindow(void)
{
	memset(_s_screen, 0, sizeof(_s_screen));
}


void V_InitDrawLine(void)
{
	// Do nothing
}


void V_ShutdownDrawLine(void)
{
	// Do nothing
}


static void NG_PutPixel(int16_t x, int16_t y, uint8_t color)
{
	if ((uint16_t)x < VIEWWINDOWWIDTH && (uint16_t)y < VIEWWINDOWHEIGHT)
		MICROFB_PIXEL(x, y) = color;
}


void V_DrawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint8_t color)
{
	int16_t dx = x1 > x0 ? x1 - x0 : x0 - x1;
	int16_t sx = x0 < x1 ? 1 : -1;
	int16_t dy = y1 > y0 ? y0 - y1 : y1 - y0;
	int16_t sy = y0 < y1 ? 1 : -1;
	int16_t err = dx + dy;

	for (;;)
	{
		NG_PutPixel(x0, y0, color);
		if (x0 == x1 && y0 == y1)
			break;
		int16_t e2 = err << 1;
		if (e2 >= dy)
		{
			err += dy;
			x0 += sx;
		}
		if (e2 <= dx)
		{
			err += dx;
			y0 += sy;
		}
	}
}


void V_DrawBackground(int16_t backgroundnum)
{
	memset(_s_screen, 0, sizeof(_s_screen));
	NG_BeginMainVramWrite();
	NG_SetFixTarget(W_GetLumpByNum(backgroundnum), FIX_TARGET_TILED_BACKGROUND);
	NG_EndMainVramWrite();
}


static void NG_RequestStaticBackground(
	static_background_t background,
	const uint16_t *wipe_source)
{
	_s_static_background_wipe_source = wipe_source;
	_s_static_background_requested = background;
	if (!_s_fix_wipe_active && _s_static_background_active == STATIC_BACKGROUND_NONE)
	{
		NG_BeginMainVramWrite();
		NG_ClearFixOverlay();
		NG_EndMainVramWrite();
	}
}


void V_DrawRawFullScreen(int16_t num)
{
	static int16_t titlepicnum = -1;
	static int16_t wimap0num = -1;
	static int16_t help2num = -1;
	if (titlepicnum < 0)
	{
		titlepicnum = W_GetNumForName("TITLEPIC");
		wimap0num = W_GetNumForName("WIMAP0");
		help2num = W_GetNumForName("HELP2");
	}

	if (num == titlepicnum || num == wimap0num || num == help2num)
	{
		if (num == titlepicnum)
			NG_RequestStaticBackground(
				STATIC_BACKGROUND_TITLE, doom_title_wipe_map);
		else if (num == wimap0num)
			NG_RequestStaticBackground(
				STATIC_BACKGROUND_WIMAP, doom_wimap_wipe_map);
		else
			NG_RequestStaticBackground(
				STATIC_BACKGROUND_HELP2, doom_help_wipe_map);
		return;
	}

	if (W_LumpLength(num) == FIX_OVERLAY_WIDTH * FIX_OVERLAY_HEIGHT * sizeof(uint16_t))
	{
		memset(_s_screen, 0, sizeof(_s_screen));
		NG_BeginMainVramWrite();
		NG_SetFixTarget(W_GetLumpByNum(num), FIX_TARGET_PAGE);
		NG_EndMainVramWrite();
		return;
	}

	const microfb_mode_t *mode = NG_MicroFramebufferMode();
	memset(_s_screen, 0, sizeof(_s_screen));

#if defined SHOW_PALETTE
	int i = 0;
	for (int y = 0; y < 16; y++)
		for (int x = 0; x < 16; x++)
			if ((uint16_t)x < mode->cols && (uint16_t)y < mode->rows)
				MICROFB_PIXEL(x, y) = i++;
#else
	const uint8_t *lump = W_GetLumpByNum(num);

	const fixed_t dxi = ((fixed_t)SCREENWIDTH << FRACBITS) / mode->cols;
	const fixed_t dyi = ((fixed_t)SCREENHEIGHT << FRACBITS) / mode->rows;

	fixed_t y = 0;
	for (uint16_t h = 0; h < mode->rows; h++)
	{
		fixed_t x = 0;
		for (uint16_t w = 0; w < mode->cols; w++)
		{
			MICROFB_PIXEL(w, h) = lump[(y >> FRACBITS) * SCREENWIDTH + (x >> FRACBITS)];
			x += dxi;
		}
		y += dyi;
	}
#endif
	if (!_s_fix_wipe_active)
	{
		NG_BeginMainVramWrite();
		NG_ClearFixOverlay();
		NG_EndMainVramWrite();
	}
}


static int16_t NG_TextX(int16_t x)
{
#if VIEWWINDOWWIDTH > FIX_OVERLAY_WIDTH
	const int16_t center_bias = (VIEWWINDOWWIDTH - FIX_OVERLAY_WIDTH) / 2;
	const int16_t right_bias  =  VIEWWINDOWWIDTH - FIX_OVERLAY_WIDTH;

	if (x >= VIEWWINDOWWIDTH - (FIX_OVERLAY_WIDTH / 2))
		x -= right_bias;
	else if (x >= FIX_OVERLAY_WIDTH / 2)
		x -= center_bias;
#endif
	return x;
}


static int16_t NG_TextY(int16_t y)
{
#if VIEWWINDOWHEIGHT > FIX_OVERLAY_HEIGHT
	const int16_t center_bias = (VIEWWINDOWHEIGHT - FIX_OVERLAY_HEIGHT) / 2;
	const int16_t bottom_bias =  VIEWWINDOWHEIGHT - FIX_OVERLAY_HEIGHT;

	if (y >= VIEWWINDOWHEIGHT - (FIX_OVERLAY_HEIGHT / 2))
		y -= bottom_bias;
	else if (y >= FIX_OVERLAY_HEIGHT / 2)
		y -= center_bias;
#endif
	return y;
}


static void NG_DrawFixCharacter(int16_t x, int16_t y, uint16_t color, uint8_t c)
{
	if ((uint16_t)x >= FIX_OVERLAY_WIDTH || (uint16_t)y >= FIX_OVERLAY_HEIGHT)
		return;

	NG_BeginMainVramWrite();
	*REG_VRAMMOD = 32;
	*REG_VRAMADDR = ADDR_FIXMAP + ((0 + 1) * 32) + y + 2 + ((uint16_t)x * 32);
	*REG_VRAMRW = color | c;
	NG_EndMainVramWrite();
}


void V_DrawFixEntry(int16_t x, int16_t y, uint16_t entry)
{
	if ((uint16_t)x >= FIX_OVERLAY_WIDTH || (uint16_t)y >= FIX_OVERLAY_HEIGHT)
		return;

	NG_BeginMainVramWrite();
	*REG_VRAMMOD = 32;
	*REG_VRAMADDR = ADDR_FIXMAP + 32u + 2u + (uint16_t)y + ((uint16_t)x * 32u);
	*REG_VRAMRW = entry;
	NG_EndMainVramWrite();
}


void V_DrawFixPatch(int16_t x, int16_t y, const uint16_t *entries, uint16_t cols, uint16_t rows)
{
	if (x >= FIX_OVERLAY_WIDTH || x + (int16_t)cols <= 0)
		return;

	NG_BeginMainVramWrite();
	for (uint16_t row = 0; row < rows; row++)
	{
		const int16_t draw_y = y + (int16_t)row;
		if ((uint16_t)draw_y >= FIX_OVERLAY_HEIGHT)
			continue;

		uint16_t first_col = x < 0 ? (uint16_t)-x : 0u;
		uint16_t last_col = cols;
		if (x + (int16_t)last_col > FIX_OVERLAY_WIDTH)
			last_col = FIX_OVERLAY_WIDTH - x;
		if (first_col >= last_col)
			continue;

		*REG_VRAMMOD = 32;
		*REG_VRAMADDR = ADDR_FIXMAP + 32u + 2u + (uint16_t)draw_y
			+ ((uint16_t)(x + (int16_t)first_col) * 32u);
		for (uint16_t col = first_col; col < last_col; col++)
			*REG_VRAMRW = entries[row * cols + col];
	}
	NG_EndMainVramWrite();
}


void V_DrawCharacter(int16_t x, int16_t y, uint16_t color, char c)
{
	NG_DrawFixCharacter(NG_TextX(x), NG_TextY(y), color, (uint8_t)c);
}


void V_DrawSTCharacter(int16_t x, int16_t y, uint16_t color, char c)
{
	V_DrawCharacter(x, y, color, c);
}


void V_DrawCharacterForeground(int16_t x, int16_t y, uint16_t color, char c)
{
	V_DrawCharacter(x, y, color, c);
}


void V_DrawString(int16_t x, int16_t y, uint16_t color, const char* s)
{
	int16_t fx = NG_TextX(x);
	int16_t fy = NG_TextY(y);
	if ((uint16_t)fy >= FIX_OVERLAY_HEIGHT || fx >= FIX_OVERLAY_WIDTH)
		return;

	while (fx < 0 && *s)
	{
		fx++;
		s++;
	}
	if (!*s)
		return;

	NG_BeginMainVramWrite();
	*REG_VRAMMOD = 32;
	*REG_VRAMADDR = ADDR_FIXMAP + 32u + 2u + (uint16_t)fy + ((uint16_t)fx * 32u);
	while (*s && fx++ < FIX_OVERLAY_WIDTH)
	{
		*REG_VRAMRW = color | (uint8_t)*s++;
	}
	NG_EndMainVramWrite();
}


void V_DrawSTString(int16_t x, int16_t y, uint16_t color, const char* s)
{
	V_DrawString(x, y, color, s);
}


void V_ClearString(int16_t y, size_t len)
{
	int16_t fy = NG_TextY(y);
	if ((uint16_t)fy >= FIX_OVERLAY_HEIGHT)
		return;

	if (len > FIX_OVERLAY_WIDTH)
		len = FIX_OVERLAY_WIDTH;

	NG_BeginMainVramWrite();
	*REG_VRAMMOD = 32;
	*REG_VRAMADDR = ADDR_FIXMAP + 32u + 2u + (uint16_t)fy;
	for (size_t x = 0; x < len; x++)
		*REG_VRAMRW = FIX_CLEAR_CHAR;
	NG_EndMainVramWrite();
}


static void NG_DrawFixedString(int16_t x, int16_t y,
	uint16_t color, const char *text)
{
	while (*text)
		NG_DrawFixCharacter(x++, y, color, (uint8_t)*text++);
}


void I_NeoGeoShowLoadingScreen(int16_t map)
{
	_s_static_background_requested = STATIC_BACKGROUND_NONE;
	_s_static_background_wipe_source = NULL;
	_s_fix_target = NULL;
	_s_fix_target_kind = FIX_TARGET_NONE;
	_s_fix_menu_palette_requested = false;
	memset(_s_screen, 0, sizeof(_s_screen));

	/*
	 * A pending melt already owns FIX as an exact copy of the old frame.
	 * Keep it intact while the new level is prepared behind it.
	 */
	if (_s_fix_wipe_active)
		return;

	NG_WaitPendingSpriteSwap();
	NG_BeginMainVramWrite();
	NG_ClearFixOverlay();
	NG_EndMainVramWrite();
	I_FinishUpdate();
	NG_WaitPendingSpriteSwap();
	NG_BeginMainVramWrite();
	NG_ClearFixOverlay();

	NG_DrawFixedString(15, 12, D_WHITE, "LOADING");
	char map_name[] = "E1M1";
	map_name[3] = '0' + map;
	NG_DrawFixedString(17, 14, D_LIGHT_RED, map_name);
	NG_EndMainVramWrite();
}


void I_InitScreenPage(void)
{
	NG_BeginMainVramWrite();
	NG_ClearFixOverlay();
	_s_fix_target = NULL;
	_s_fix_target_kind = FIX_TARGET_NONE;
	NG_EndMainVramWrite();
}


void I_InitScreenPages(void)
{
	NG_BeginMainVramWrite();
	NG_ClearFixOverlay();
	_s_fix_target = NULL;
	_s_fix_target_kind = FIX_TARGET_NONE;
	NG_EndMainVramWrite();
}


static void NG_DrawFixWipeMask(void)
{
	const microfb_mode_t *mode = NG_MicroFramebufferMode();

	*REG_VRAMMOD = 32;
	for (uint16_t y = 0; y < FIX_WIPE_HEIGHT; y++)
	{
		*REG_VRAMADDR = ADDR_FIXMAP + FIX_WIPE_Y_OFFSET + y;
		for (uint16_t x = 0; x < FIX_WIPE_WIDTH; x++)
		{
			const uint16_t sx = (x * mode->cols) / FIX_WIPE_WIDTH;
			const uint16_t sy = (y * mode->rows) / FIX_WIPE_HEIGHT;
			uint8_t color = MICROFB_PIXEL(sx, sy);
			if ((color & 0x0fu) == 0)
				color = doom_fix_wipe_zero_pen_map[color >> 4];
			*REG_VRAMRW = (uint16_t)color << 8;
		}
	}
	_s_fix_page_active = true;
}


static void NG_UploadFixWipeMap(const uint16_t *map)
{
	*REG_VRAMMOD = 32;
	for (uint16_t y = 0; y < FIX_WIPE_HEIGHT; y++)
	{
		*REG_VRAMADDR = ADDR_FIXMAP + FIX_WIPE_Y_OFFSET + y;
		for (uint16_t x = 0; x < FIX_WIPE_WIDTH; x++)
			*REG_VRAMRW = map[y * FIX_WIPE_WIDTH + x];
	}
	_s_fix_page_active = true;
}


void wipe_StartScreen(void)
{
	if (_s_fix_wipe_active)
		return;

	_s_fix_wipe_active = true;
	_s_fix_wipe_restore_menu_palette = _s_fix_menu_palette_requested;
	_s_fix_menu_palette_requested = false;
	NG_WaitPendingSpriteSwap();
	NG_WaitVBlankStart();
	NG_BeginMainVramWrite();
	NG_ApplyPendingPalettes();

	if (_s_static_background_active != STATIC_BACKGROUND_NONE
		&& _s_static_background_wipe_source)
		NG_UploadFixWipeMap(_s_static_background_wipe_source);
	else
		NG_DrawFixWipeMask();

	_s_fix_target = NULL;
	_s_fix_target_kind = FIX_TARGET_NONE;
	NG_EndMainVramWrite();
}


static int16_t *wipe_y_lookup;


static boolean wipe_ScreenWipe(int16_t ticks)
{
	boolean done = true;

	*REG_VRAMMOD = 1;

	while (ticks--)
	{
		for (int16_t i = 0; i < FIX_WIPE_WIDTH; i++)
		{
			if (wipe_y_lookup[i] < 0)
			{
				wipe_y_lookup[i]++;
				done = false;
				continue;
			}

			// scroll down columns, which are still visible
			if (wipe_y_lookup[i] < FIX_WIPE_HEIGHT)
			{
				int16_t dy = 1;
				// At most dy shall be so that the column is shifted just invisible.
				if (wipe_y_lookup[i] + dy >= FIX_WIPE_HEIGHT)
					dy = FIX_WIPE_HEIGHT - wipe_y_lookup[i];

				int16_t s = FIX_WIPE_Y_OFFSET + FIX_WIPE_HEIGHT - 1 - dy;
				int16_t d = FIX_WIPE_Y_OFFSET + FIX_WIPE_HEIGHT - 1;
				const uint16_t column = (uint16_t)i * 32u;

				for (int16_t j = FIX_WIPE_HEIGHT - wipe_y_lookup[i] - dy; j; j--)
				{
					*REG_VRAMADDR = ADDR_FIXMAP + column + (uint16_t)s;
					uint16_t entry = *REG_VRAMRW;
					*REG_VRAMADDR = ADDR_FIXMAP + column + (uint16_t)d;
					*REG_VRAMRW = entry;
					s--;
					d--;
				}

				*REG_VRAMADDR = ADDR_FIXMAP + column + FIX_WIPE_Y_OFFSET + (uint16_t)wipe_y_lookup[i];
				for (int16_t j = 0; j < dy; j++)
				{
					uint16_t entry = FIX_CLEAR_CHAR;
					if (_s_fix_target && i > 0 && i <= FIX_OVERLAY_WIDTH)
						entry = NG_FixTargetEntry(i - 1, wipe_y_lookup[i] + j);
					*REG_VRAMRW = entry;
				}

				wipe_y_lookup[i] += dy;
				done = false;
			}
		}
	}

	return done;
}


static void wipe_initMelt()
{
	wipe_y_lookup[0] = -(M_Random() % 16);
	for (int16_t i = 1; i < FIX_WIPE_WIDTH; i++)
	{
		int16_t r = (M_Random() % 3) - 1;

		wipe_y_lookup[i] = wipe_y_lookup[i - 1] + r;

		if (wipe_y_lookup[i] > 0)
			wipe_y_lookup[i] = 0;
		else if (wipe_y_lookup[i] == -16)
			wipe_y_lookup[i] = -15;
	}
}


static void NG_FinishFixWipe(void)
{
	_s_fix_menu_palette_requested = _s_fix_wipe_restore_menu_palette;
	NG_WaitPendingSpriteSwap();
	NG_WaitVBlankStart();
	NG_BeginMainVramWrite();
	NG_ApplyPendingPalettes();
	NG_EndMainVramWrite();
	_s_fix_wipe_active = false;
	M_NeoGeoInvalidateMenu();
	M_Drawer();
}


void D_Wipe(void)
{
	wipe_y_lookup = Z_TryMallocStatic(FIX_WIPE_WIDTH * sizeof(int16_t));
	if (!wipe_y_lookup)
	{
		NG_WaitPendingSpriteSwap();
		NG_BeginMainVramWrite();
		if (_s_fix_target)
			NG_UploadFixTarget();
		else
			NG_ClearFixOverlay();
		NG_EndMainVramWrite();
		NG_FinishFixWipe();
		return;
	}

	_s_fix_menu_palette_requested = false;
	I_FinishUpdate();
	NG_WaitPendingSpriteSwap();

	wipe_initMelt();

	boolean done;
	int32_t wipestart = I_GetTime() - 1;

	do
	{
		int32_t nowtime;
		int16_t tics;
		do
		{
			nowtime = I_GetTime();
			tics = nowtime - wipestart;
		} while (!tics);

		wipestart = nowtime;
		NG_BeginMainVramWrite();
		done = wipe_ScreenWipe(tics);
		NG_EndMainVramWrite();

		NG_WaitVBlankStart();
		NG_UploadFixOverlay();

	} while (!done);

	NG_BeginMainVramWrite();
	if (_s_fix_target)
		_s_fix_page_active = true;
	else
		NG_ClearFixOverlay();
	NG_EndMainVramWrite();

	_s_fix_target = NULL;
	_s_fix_target_kind = FIX_TARGET_NONE;
	Z_Free(wipe_y_lookup);

	NG_FinishFixWipe();
}
