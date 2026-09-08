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
 *      Map Objects, MObj, definition and handling.
 *
 *-----------------------------------------------------------------------------*/

#ifndef __P_MOBJ__
#define __P_MOBJ__

// Basics.
#include "tables.h"
#include "m_fixed.h"

// We need the thinker_t stuff.
#include "d_think.h"

// We need the WAD data structure for Map things,
// from the THINGS lump.
#include "doomdata.h"

// States are tied to finite states are
//  tied to animation frames.
// Needs precompiled tables/data structures.
#include "info.h"

//
// NOTES: mobj_t
//
// mobj_ts are used to tell the refresh where to draw an image,
// tell the world simulation when objects are contacted,
// and tell the sound driver how to position a sound.
//
// The refresh uses the next and prev links to follow
// lists of things in sectors as they are being drawn.
// The sprite, frame, and angle elements determine which patch_t
// is used to draw the sprite if it is visible.
// The sprite and frame values are allmost allways set
// from state_t structures.
// The statescr.exe utility generates the states.h and states.c
// files that contain the sprite/frame numbers from the
// statescr.txt source file.
// The xyz origin point represents a point at the bottom middle
// of the sprite (between the feet of a biped).
// This is the default origin position for patch_ts grabbed
// with lumpy.exe.
// A walking creature will have its z equal to the floor
// it is standing on.
//
// The sound code uses the x,y, and subsector fields
// to do stereo positioning of any sound effited by the mobj_t.
//
// The play simulation uses the blocklinks, x,y,z, radius, height
// to determine when mobj_ts are touching each other,
// touching lines in the map, or hit by trace lines (gunshots,
// lines of sight, etc).
// The mobj_t->flags element has various bit flags
// used by the simulation.
//
// Every mobj_t is linked into a single sector
// based on its origin coordinates.
// The subsector_t is found with R_PointInSubsector(x,y),
// and the sector_t can be found with subsector->sector.
// The sector links are only used by the rendering code,
// the play simulation does not care about them at all.
//
// Any mobj_t that needs to be acted upon by something else
// in the play world (block movement, be shot, etc) will also
// need to be linked into the blockmap.
// If the thing has the MF_NOBLOCK flag set, it will not use
// the block links. It can still interact with other things,
// but only as the instigator (missiles will run into other
// things, but nothing can run into a missile).
// Each block in the grid is 128*128 units, and knows about
// every line_t that it contains a piece of, and every
// interactable mobj_t that has its origin contained.
//
// A valid mobj_t is a mobj_t that has the proper subsector_t
// filled in for its xy coordinates and is linked into the
// sector from which the subsector was made, or has the
// MF_NOSECTOR flag set (the subsector_t needs to be valid
// even if MF_NOSECTOR is set), and is linked into a blockmap
// block or has the MF_NOBLOCKMAP flag set.
// Links should only be modified by the P_[Un]SetThingPosition()
// functions.
// Do not change the MF_NO? flags while a thing is valid.
//
// Any questions?
//

//
// Misc. mobj flags
//

// Call P_SpecialThing when touched.
#define MF_SPECIAL      0x00000001UL
// Blocks.
#define MF_SOLID        0x00000002UL
// Can be hit.
#define MF_SHOOTABLE    0x00000004UL
// Don't use the sector links (invisible but touchable).
#define MF_NOSECTOR     0x00000008UL
// Don't use the blocklinks (inert but displayable)
#define MF_NOBLOCKMAP   0x00000010UL

// Not to be activated by sound, deaf monster.
#define MF_AMBUSH       0x00000020UL
// Will try to attack right back.
#define MF_JUSTHIT      0x00000040UL
// Will take at least one step before attacking.
#define MF_JUSTATTACKED 0x00000080UL

// Don't apply gravity (every tic),
//  that is, object will float, keeping current height
//  or changing it actively.
#define MF_NOGRAVITY    0x00000200UL

// Movement flags.
// This allows jumps from high places.
#define MF_DROPOFF      0x00000400UL
// For players, will pick up items.
#define MF_PICKUP       0x00000800UL
// Player cheat. ???
#define MF_NOCLIP       0x00001000UL

// Don't hit same species, explode on block.
// Player missiles as well as fireballs of various kinds.
#define MF_MISSILE      0x00010000UL
// Dropped by a demon, not level spawned.
// E.g. ammo clips dropped by dying former humans.
#define MF_DROPPED      0x00020000UL
// Use fuzzy draw (shadow demons or spectres),
//  temporary player invisibility powerup.
#define MF_SHADOW       0x00040000UL
// Flag: don't bleed when shot (use puff),
//  barrels and shootable furniture shall not bleed.
#define MF_NOBLOOD      0x00080000UL
// Don't stop moving halfway off a step,
//  that is, have dead bodies slide down all the way.
#define MF_CORPSE       0x00100000UL

// On kill, count this enemy object
//  towards intermission kill total.
// Happy gathering.
#define MF_COUNTKILL    0x00400000UL

// On picking up, count this item object
//  towards intermission item total.
#define MF_COUNTITEM    0x00800000UL

#define MF_POOLED       0x10000000UL


// Map Object definition.
//
//
// killough 2/20/98:
//
// WARNING: Special steps must be taken in p_saveg.c if C pointers are added to
// this mobj_s struct, or else savegames will crash when loaded. See p_saveg.c.
// Do not add "struct mobj_s *fooptr" without adding code to p_saveg.c to
// convert the pointers to ordinals and back for savegames. This was the whole
// reason behind monsters going to sleep when loading savegames (the "target"
// pointer was simply nullified after loading, to prevent Doom from crashing),
// and the whole reason behind loadgames crashing on savegames of AV attacks.
//

// killough 9/8/98: changed some fields to shorts,
// for better memory usage (if only for cache).
/* cph 2006/08/28 - move Prev[XYZ] fields to the end of the struct. Add any
 * other new fields to the end, and make sure you don't break savegames! */

#if defined NEOGEO_COMPACT_MSECNODES && !defined __NGDEVKIT__
#error NEOGEO_COMPACT_MSECNODES requires the Neo Geo Work-RAM ABI
#endif

#if defined NEOGEO_COMPACT_MSECNODES
typedef uint16_t msecnode_link_t;
#else
typedef struct msecnode_s __far* msecnode_link_t;
#endif

#define MSECNODE_NULL ((msecnode_link_t)0)

typedef struct mobj_s
{
    // List: thinker links.
    thinker_t           thinker;

    // Info for drawing: position.
    fixed_t             x;
    fixed_t             y;
    fixed_t             z;

    // More list: links in sector (if needed)
    struct mobj_s __far*      snext;
    struct mobj_s __far*__far*     sprev; // killough 8/10/98: change to ptr-to-ptr

    //More drawing info: to determine current sprite.
    angle_t             angle;  // orientation

    // Interaction info, by BLOCKMAP.
    // Links in blocks (if needed).
#if defined NEOGEO_COMPACT_BLOCKMAP
    uint8_t             bnext; // Index into _g_thingPool, or MOBJ_NO_INDEX.
#else
    uint16_t            bnext; // Index into _g_thingPool, or MOBJ_NO_INDEX.
#endif

    struct subsector_s __far* subsector;

    // The closest interval over all contacted Sectors.
    fixed_t             floorz;
    fixed_t             ceilingz;

    // killough 11/98: the lowest floor over all contacted Sectors.
    fixed_t             dropoffz;

    // For movement checking.
    fixed_t             radius;
    fixed_t             height;

    // Momentums, used to update position.
    fixed_t             momx;
    fixed_t             momy;
    fixed_t             momz;

    const state_t*      state;
    uint32_t            flags;

    // Thing being chased/attacked (or NULL),
    // also the originator for missiles.
    struct mobj_s __far*      target;

    // new field: last known enemy -- killough 2/15/98
    struct mobj_s __far*      lastenemy;

                                       // phares 3/17/98
    // a linked list of sectors where this object appears
    msecnode_link_t touching_sectorlist;

    uint16_t            frame;  // might be ORed with FF_FULLBRIGHT

    int16_t             health;

    int16_t             tics;   // state tic counter

    // killough 9/9/98: How long a monster pursues a target.
    uint16_t            pursuecount;

    int16_t             movecount;      // when 0, select a new dir

    // Reaction time: if non 0, don't attack yet.
    // Used by player to freeze a bit after teleporting.
    int16_t             reactiontime;

    spritenum_t         sprite; // used to find patch_t and flip value

    mobjtype_t          type;

    // Movement direction, movement generation (zig-zagging).

    uint8_t            movedir;

    // If >0, the current target will be chased no
    // matter what (even if shot by another object)
    uint8_t            threshold;

    // SEE WARNING ABOVE ABOUT POINTER FIELDS!!!
} mobj_t;

#if defined NEOGEO_COMPACT_MSECNODES
_Static_assert(sizeof(mobj_t) == 108,
               "compact Neo Geo mobj_t must be 108 bytes");
_Static_assert(_Alignof(mobj_t) >= 2,
               "compact Neo Geo mobj_t must stay 68000-aligned");
#endif

#if defined NEOGEO_COMPACT_BLOCKMAP
typedef uint8_t mobjindex_t;
#define MOBJ_NO_INDEX UINT8_MAX
#else
typedef uint16_t mobjindex_t;
#define MOBJ_NO_INDEX UINT16_MAX
#endif

// External declarations (fomerly in p_local.h) -- killough 5/2/98

#define VIEWHEIGHT      (41*FRACUNIT)


#define ONFLOORZ        INT32_MIN
#define ONCEILINGZ      INT32_MAX


mobj_t __far* P_SpawnMobj(fixed_t x, fixed_t y, fixed_t z, mobjtype_t type);
void    P_RemoveMobj(mobj_t __far* th);
boolean P_SetMobjState(mobj_t __far* mobj, statenum_t state);

void    P_MobjThinker(mobj_t __far* mobj);

void    P_SpawnPuff(fixed_t x, fixed_t y, fixed_t z);
void    P_SpawnBlood(fixed_t x, fixed_t y, fixed_t z, int16_t damage);
mobj_t __far* P_SpawnMissile(mobj_t __far* source, mobj_t __far* dest, mobjtype_t type);
void    P_SpawnPlayerMissile(mobj_t __far* source);
mobjtype_t P_MapThingMobjType(const mapthing_t __far* mthing);
mobj_t __far* P_SpawnMapThing(const mapthing_t __far* mthing,
                              mobjtype_t type,
                              boolean count_totals);

struct player_s* P_MobjIsPlayer(const mobj_t __far* mobj);

#endif
