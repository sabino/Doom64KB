/* Emacs style mode select   -*- C++ -*-
 *-----------------------------------------------------------------------------
 *
 *
 *  PrBoom: a Doom port merged with LxDoom and LSDLDoom
 *  based on BOOM, a modified and improved DOOM engine
 *  Copyright (C) 1999 by
 *  id Software, Chi Hoang, Lee Killough, Jim Flynn, Rand Phares, Ty Halderman
 *  Copyright (C) 1999-2001 by
 *  Jess Haas, Nicolas Kalkhof, Colin Phipps, Florian Schulze
 *  Copyright 2005, 2006 by
 *  Florian Schulze, Colin Phipps, Neil Stevens, Andrey Budko
 *  Copyright 2023-2026 by
 *  Frenkel Smeijers
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
 *      Handles WAD file header, directory, lump I/O.
 *
 *-----------------------------------------------------------------------------
 */

// use config.h if autoconf made one -- josh
#ifdef HAVE_CONFIG_H
#include "config.h"
#endif
#ifdef HAVE_UNISTD_H
#include <unistd.h>
#endif

#include <stdint.h>

#include "compiler.h"
#include "d_player.h"
#include "doomtype.h"
#include "i_system.h"

#include "w_wad.h"

#include "globdata.h"


//
// TYPES
//

typedef struct
{
  int32_t  filepos;
  uint16_t size;
  int16_t  filler;        // always zero
  char name[8];
} filelump_t;


//
// GLOBALS
//

#if defined __WATCOMC__
static unsigned char doom_iwad[1 * 1014 * 1024];
static unsigned char doom_iwad_maps[1 * 1014 * 1024];
#elif defined __DJGPP__
#include "doom64kl.h"
#include "doommapl.h"
#elif defined __NGDEVKIT__
#include "doom64ng.h"
#include "doom64nm.h"
#else
#error unsupported compiler
#endif

static filelump_t __far* fileinfo;


//
// LUMP BASED ROUTINES.
//

typedef struct
{
  char identification[4]; // Should be "IWAD" or "PWAD".
  int16_t  numlumps;
  int16_t  filler;        // always zero
  int32_t  infotableofs;
} wadinfo_t;


#if !defined WAD_FILE
#define WAD_FILE "DOOM1.WAD"
#endif


void W_Init(void)
{
	printf("\tadding " WAD_FILE "\n");
	printf("\tshareware version.\n");

#if defined __WATCOMC__
	FILE *fileWAD;

	fileWAD = fopen(WAD_FILE, "rb");
	if (fileWAD == NULL)
		I_Error("Can't open " WAD_FILE ".");

	fread(doom_iwad, sizeof(doom_iwad), 1, fileWAD);
	fclose(fileWAD);

	fileWAD = fopen(MAP_WAD_FILE, "rb");
	if (fileWAD == NULL)
		I_Error("Can't open " MAP_WAD_FILE ".");

	fread(doom_iwad_maps, sizeof(doom_iwad_maps), 1, fileWAD);
	fclose(fileWAD);
#endif

	wadinfo_t *header = (wadinfo_t*)&doom_iwad[0];
	fileinfo = (filelump_t __far*)&doom_iwad[header->infotableofs];
}


const char __far* PUREFUNC W_GetNameForNum(int16_t num)
{
	return fileinfo[num].name;
}


//
// W_LumpLength
// Returns the buffer size needed to load the given lump.
//

uint16_t PUREFUNC W_LumpLength(int16_t num)
{
	return fileinfo[num].size;
}


uint16_t PUREFUNC W_MapLumpLength(int16_t num)
{
	wadinfo_t *mapheader = (wadinfo_t*)&doom_iwad_maps[0];
	filelump_t *mapfileinfo = (filelump_t *)&doom_iwad_maps[mapheader->infotableofs];
	return mapfileinfo[num].size;
}


// W_GetNumForName
// bombs out if not found.
//
int16_t PUREFUNC W_GetNumForName(const char *name)
{
	char name8[8];
	strncpy(name8, name, sizeof(name8));

	wadinfo_t *header = (wadinfo_t*)&doom_iwad[0];

	for (int16_t i = 0; i < header->numlumps; i++)
	{
		if (Z_EqualNames(fileinfo[i].name, name8))
		{
			return i;
		}
	}

	I_Error("W_GetNumForName: %.8s not found", name);
	return -1;
}


int16_t PUREFUNC W_GetMapNumForName(const char *name)
{
	char name8[8];
	strncpy(name8, name, sizeof(name8));

	wadinfo_t *mapheader = (wadinfo_t*)&doom_iwad_maps[0];
	filelump_t *mapfileinfo = (filelump_t *)&doom_iwad_maps[mapheader->infotableofs];

	for (int16_t i = 0; i < mapheader->numlumps; i++)
	{
		if (Z_EqualNames(mapfileinfo[i].name, name8))
		{
			return i;
		}
	}

	I_Error("W_GetMapNumForName: %.8s not found", name);
	return -1;
}


void W_ReadLumpByNum(int16_t num, void __far* ptr)
{
	const filelump_t __far* lump = &fileinfo[num];
	memcpy(ptr, &doom_iwad[lump->filepos], lump->size);
}


const void __far* PUREFUNC W_GetLumpByNum(int16_t num)
{
	const filelump_t __far* lump = &fileinfo[num];
	return &doom_iwad[lump->filepos];
}


const void __far* PUREFUNC W_GetMapLumpByNum(int16_t num)
{
	wadinfo_t *mapheader = (wadinfo_t*)&doom_iwad_maps[0];
	filelump_t *mapfileinfo = (filelump_t *)&doom_iwad_maps[mapheader->infotableofs];
	const filelump_t *lump = &mapfileinfo[num];
	return &doom_iwad_maps[lump->filepos];
}
