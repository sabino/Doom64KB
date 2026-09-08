/* Emacs style mode select   -*- C++ -*-
 *-----------------------------------------------------------------------------
 *
 *
 *  PrBoom: a Doom port merged with LxDoom and LSDLDoom
 *  based on BOOM, a modified and improved DOOM engine
 *  Copyright (C) 1999 by
 *  id Software, Chi Hoang, Lee Killough, Jim Flynn, Rand Phares, Ty Halderman
 *  Copyright (C) 1999-2000 by
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
 *  Refresh of things, i.e. objects represented by sprites.
 *
 *-----------------------------------------------------------------------------*/

#include <stdint.h>

#include "compiler.h"
#include "d_player.h"
#include "w_wad.h"
#include "r_main.h"
#include "r_things.h"
#include "v_video.h"
#include "i_system.h"

#include "globdata.h"

#if defined __NGDEVKIT__ && defined NEOGEO_ROM_SPRITE_DEFS
#include "neogeo/assets/generated/doom_sprite_defs.h"
#endif


// variables used to look up and range check thing_t sprites patches
#if defined __NGDEVKIT__ && defined NEOGEO_ROM_SPRITE_DEFS
const spritedef_t *sprites;
#else
const spriteframe_t *sprites;
#endif

#if defined __NGDEVKIT__ && defined NEOGEO_ROM_SPRITE_DEFS

void R_InitSprites(void)
{
  sprites = doom_sprite_definitions;
}


void R_InitSpriteLumps(void)
{
}

#else

void R_InitSprites(void)
{
	sprites = W_GetLumpByName("SPRITES");
}


void R_InitSpriteLumps(void)
{
	// Do nothing
}

#endif
