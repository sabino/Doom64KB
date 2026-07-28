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
 *      Neo Geo implementation of i_system.h
 *
 *-----------------------------------------------------------------------------*/

#include <stdarg.h>
#include <stdio.h>
#include <ngdevkit/bios-ram.h>
#include <ngdevkit/registers.h>

#include "doomdef.h"
#include "doomtype.h"
#include "compiler.h"
#include "d_main.h"
#include "i_system.h"
#include "i_video.h"

#include "neogeo/assets/generated/audio/doom_audio_generated.h"

#include "globdata.h"


//#define TIMEDEMO


void I_InitGraphicsHardwareSpecificCode(void);
void I_ShutdownGraphics(void);


static boolean isGraphicsModeSet = false;


//**************************************************************************************
//
// Screen code
//

void I_InitGraphics(void)
{
	I_InitGraphicsHardwareSpecificCode();
	isGraphicsModeSet = true;
}


//**************************************************************************************
//
// Keyboard code
//

static boolean isKeyboardIsrSet = false;


void I_InitKeyboard(void)
{
	isKeyboardIsrSet = true;
}


static void I_PostEvent(boolean keyup, int16_t data1)
{
	event_t ev;
	ev.type  = keyup ? ev_keyup : ev_keydown;
	ev.data1 = data1;
	D_PostEvent(&ev);
}


#define KB_MATRIX_SIZE 2

void I_StartTic(void)
{
	static uint8_t kb_matrix[KB_MATRIX_SIZE * 2];
	static uint8_t *kb_matrix_cur = &kb_matrix[0];
	static uint8_t *kb_matrix_prv = &kb_matrix[KB_MATRIX_SIZE];

	uint8_t *tmp = kb_matrix_cur;
	kb_matrix_cur = kb_matrix_prv;
	kb_matrix_prv = tmp;

	kb_matrix_cur[0] = *REG_P1CNT;
	kb_matrix_cur[1] = *REG_STATUS_B;

	uint8_t diff;
	diff = kb_matrix_prv[0] ^ kb_matrix_cur[0];
	if (diff & CNT_UP)    I_PostEvent(kb_matrix_cur[0] & CNT_UP,    KEYD_UP);		// Up
	if (diff & CNT_DOWN)  I_PostEvent(kb_matrix_cur[0] & CNT_DOWN,  KEYD_DOWN);		// Down
	if (diff & CNT_LEFT)  I_PostEvent(kb_matrix_cur[0] & CNT_LEFT,  KEYD_LEFT);		// Left
	if (diff & CNT_RIGHT) I_PostEvent(kb_matrix_cur[0] & CNT_RIGHT, KEYD_RIGHT);	// Right
	if (diff & CNT_A)     I_PostEvent(kb_matrix_cur[0] & CNT_A,     KEYD_A);		// A
	if (diff & CNT_B)     I_PostEvent(kb_matrix_cur[0] & CNT_B,     KEYD_B);		// S
	if (diff & CNT_C)     I_PostEvent(kb_matrix_cur[0] & CNT_C,     KEYD_L);		// Q
	if (diff & CNT_D)     I_PostEvent(kb_matrix_cur[0] & CNT_D,     KEYD_R);		// W

	diff = kb_matrix_prv[1] ^ kb_matrix_cur[1];
	if (diff & CNT_START1) I_PostEvent(kb_matrix_cur[1] & CNT_START1, KEYD_START);	// 1
	if (diff & CNT_START2) I_PostEvent(kb_matrix_cur[1] & CNT_START2, KEYD_SELECT);	// 2
}


//**************************************************************************************
//
// Audio
//

#define RESET_SOUND_DRIVER 3

_Static_assert(NUMSFX - 1 == DOOM_SOUND_SFX_COUNT,
	"Neo Geo sound command table must match sfxenum_t");
_Static_assert(NUMMUSIC - 1 == DOOM_SOUND_MUSIC_COUNT,
	"Neo Geo music command table must match musicenum_t");


void DMX_Play(sfxenum_t id)
{
	if (id > sfx_None && id < NUMSFX)
		*REG_SOUND = DOOM_SOUND_SFX_COMMAND(id);
}


void DMX_PlayMusic(musicenum_t id, uint8_t looping)
{
	uint8_t command;
	UNUSED(looping);

	if (id <= mus_None || id >= NUMMUSIC)
		return;

	command = DOOM_SOUND_MUSIC_COMMAND(id);
	*REG_SOUND = command;
}


void DMX_StopMusic(void)
{
	*REG_SOUND = DOOM_SOUND_CMD_MUSIC_STOP;
}


void DMX_SetSfxVolume(uint8_t volume)
{
	if (volume > 15u)
		volume = 15u;
	*REG_SOUND = DOOM_SOUND_SFX_VOLUME_COMMAND(volume);
}


void DMX_SetMusicVolume(uint8_t volume)
{
	if (volume > 15u)
		volume = 15u;
	*REG_SOUND = DOOM_SOUND_MUSIC_VOLUME_COMMAND(volume);
}


void DMX_Init(void)
{
	*REG_SOUND = RESET_SOUND_DRIVER;
}


void DMX_Init2(void)
{
	// Do nothing
}


void DMX_Shutdown(void)
{
	*REG_SOUND = DOOM_SOUND_CMD_ALL_OFF;
}


//**************************************************************************************
//
// Returns time in 1/35th second tics.
//

static volatile int32_t ticcount;

static boolean isTimerSet;


void rom_callback_VBlank() {
	ticcount++;
#if defined NEOGEO_SPRITE_MICROFB
	I_NeoGeoVBlank();
#endif
}


int32_t I_GetTime(void)
{
	return ticcount * TICRATE / 60;
}


void I_InitTimer(void)
{
	isTimerSet = true;
}


static void I_ShutdownTimer(void)
{
	// Do nothing
}


//**************************************************************************************
//
// Memory
//

// The Neo Geo has 64 KB of RAM.  The sprite framebuffer backend needs a
// smaller static/heap split than the original FIX framebuffer build.
#if !defined NEOGEO_HEAP_SIZE
#if defined NEOGEO_SPRITE_MICROFB
#define NEOGEO_HEAP_SIZE 44000
#else
// 53648 is the maximum value with which this program can still be compiled.
// Leave 2 KB for the stack.
#define NEOGEO_HEAP_SIZE (53648-2*1024)
#endif
#endif

#define HEAP_SIZE NEOGEO_HEAP_SIZE


uint8_t __far* I_ZoneBase(uint32_t *heapSize)
{
	static uint8_t heap[HEAP_SIZE];
	uint32_t paragraphs = HEAP_SIZE / PARAGRAPH_SIZE;
	uint8_t *ptr = heap;

	// align ptr
	uint32_t m = (uint32_t) ptr;
	if ((m & (PARAGRAPH_SIZE - 1)) != 0)
	{
		paragraphs--;
		while ((m & (PARAGRAPH_SIZE - 1)) != 0)
			m = (uint32_t) ++ptr;
	}

	*heapSize = paragraphs * PARAGRAPH_SIZE;
	return ptr;
}


//**************************************************************************************
//
// Exit code
//

static void I_Shutdown(void)
{
	if (isGraphicsModeSet)
		I_ShutdownGraphics();

	I_ShutdownSound();

	if (isTimerSet)
		I_ShutdownTimer();

	if (isKeyboardIsrSet)
	{
		// Do nothing
	}
}


void I_Quit(void)
{
	I_Shutdown();

	for (;;) {}
	exit(0);
}


static void ng_printf(const char *text)
{
	int x = 0;
	int y = 0;
	MMAP_PALBANK1[1]     = 0x7FFF;
	MMAP_PALBANK1[2]     = 0x8BBB;
	MMAP_PALBANK1[0xfff] = 0x8000;

	*REG_VRAMMOD = 0x200;
	for (int i = 0; i < 381; i++)
	{
		*REG_VRAMADDR = ADDR_SCB2 + i;
		*REG_VRAMRW = 0x0000;
		*REG_VRAMRW = 0x0000;
		*REG_VRAMRW = 0x0000;
	}

	*REG_VRAMMOD = 32;
	for (int cy = 0; cy < 28; cy++)
	{
		*REG_VRAMADDR = ADDR_FIXMAP + ((0 + 1) * 32) + cy + 2;
		for (int cx = 0; cx < 38; cx++)
			*REG_VRAMRW = 0x3000 | ' ';
	}
	*REG_VRAMADDR = ADDR_FIXMAP + ((x + 1) * 32) + y + 2;
	*REG_VRAMMOD = 32;
	while (*text)
	{
		if (y >= 28)
			break;
		if (*text == '\n')
		{
			text++;
			x = 0;
			y++;
			if (y < 28)
				*REG_VRAMADDR = ADDR_FIXMAP + ((x + 1) * 32) + y + 2;
		}
		else
		{
			*REG_VRAMRW = 0x3000 | *text++;
			x++;
			if (x == 38)
			{
				x = 0;
				y++;
				if (y < 28)
					*REG_VRAMADDR = ADDR_FIXMAP + ((x + 1) * 32) + y + 2;
			}
		}
	}
}


void I_Error(const char *error, ...)
{
	va_list argptr;

	I_Shutdown();

	va_start(argptr, error);
	char buffer[160];
	vsnprintf(buffer, sizeof(buffer), error, argptr);
	buffer[sizeof(buffer) - 1] = '\0';
	ng_printf(buffer);
	va_end(argptr);

	for (;;) {}
	exit(1);
}


int main(void)
{
#if defined TIMEDEMO
	int argc = 3;
	const char * const argv[] = {"Doom64KB", "-timedemo", "demo3"};
#else
	int argc = 1;
	const char * const argv[] = {"Doom64KB"};
#endif
	D_DoomMain(argc, argv);

	return 0;
}
