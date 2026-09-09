set -e

NGDEVKIT_PREFIX="${NGDEVKIT_PREFIX:-$(dirname "$(dirname "$(readlink -f "$(command -v m68k-neogeo-elf-gcc)")")")}"
NGDEVKIT_DATA_DIR="${NGDEVKIT_DATA_DIR:-$NGDEVKIT_PREFIX/share/ngdevkit}"
GNGEO_DATA_FILE="${GNGEO_DATA_FILE:-$NGDEVKIT_PREFIX/share/ngdevkit-gngeo/gngeo_data.zip}"

mkdir -p neogeo/rom
mkdir -p neogeo/assets/generated
python3 -B tools/gen_neogeo_reciprocal.py

fix_ui_inputs="
  tools/gen_neogeo_fix_menu.py
  DOOM64TB.WAD
  doom64ng.h
  neogeo/assets/doom64kb.fix
"
fix_ui_outputs="
  neogeo/assets/doom-menu.fix
  neogeo/assets/doom-title.c1
  neogeo/assets/doom-title.c2
  neogeo/doom_fix_menu_assets.h
  neogeo/doom_fix_intermission_assets.h
  neogeo/doom_fix_menu_palette.h
  neogeo/doom_fix_wipe_assets.h
  neogeo/doom_title_assets.h
"
fix_ui_needs_rebuild=false
for asset in $fix_ui_outputs
do
  if [ ! -f "$asset" ]
  then
    fix_ui_needs_rebuild=true
    break
  fi

  for source in $fix_ui_inputs
  do
    if [ "$source" -nt "$asset" ]
    then
      fix_ui_needs_rebuild=true
      break 2
    fi
  done
done

if [ "$fix_ui_needs_rebuild" = true ]
then
  python3 -B tools/gen_neogeo_fix_menu.py \
    --iwad DOOM64TB.WAD \
    --embedded-wad-header doom64ng.h \
    --base-srom neogeo/assets/doom64kb.fix \
    --out-fix neogeo/assets/doom-menu.fix \
    --out-menu-header neogeo/doom_fix_menu_assets.h \
    --out-intermission-header neogeo/doom_fix_intermission_assets.h \
    --out-menu-palette-header neogeo/doom_fix_menu_palette.h \
    --out-wipe-header neogeo/doom_fix_wipe_assets.h \
    --out-title-c1 neogeo/assets/doom-title.c1 \
    --out-title-c2 neogeo/assets/doom-title.c2 \
    --out-title-header neogeo/doom_title_assets.h
fi

SPRITE_DEFS_HEADER="neogeo/assets/generated/doom_sprite_defs.h"
if [ ! -f "$SPRITE_DEFS_HEADER" ] || \
   [ tools/gen_neogeo_sprite_defs.py -nt "$SPRITE_DEFS_HEADER" ] || \
   [ DOOM64TB.WAD -nt "$SPRITE_DEFS_HEADER" ] || \
   [ doom64ng.h -nt "$SPRITE_DEFS_HEADER" ] || \
   [ info.c -nt "$SPRITE_DEFS_HEADER" ]
then
  python3 tools/gen_neogeo_sprite_defs.py \
    --iwad DOOM64TB.WAD \
    --embedded-wad-header doom64ng.h \
    --sprite-source info.c \
    --output "$SPRITE_DEFS_HEADER"
  python3 -B tools/verify_neogeo_sprite_defs.py \
    --iwad DOOM64TB.WAD \
    --embedded-wad-header doom64ng.h \
    --sprite-source info.c \
    --generated-header "$SPRITE_DEFS_HEADER"
fi

SECTOR_LINES_HEADER="neogeo/assets/generated/doom_sector_lines.h"
if [ ! -f "$SECTOR_LINES_HEADER" ] || \
   [ tools/gen_neogeo_sector_lines.py -nt "$SECTOR_LINES_HEADER" ] || \
   [ DOOM64TB.WAD -nt "$SECTOR_LINES_HEADER" ]
then
  python3 tools/gen_neogeo_sector_lines.py \
    --iwad DOOM64TB.WAD \
    --output "$SECTOR_LINES_HEADER"
fi

WALL_COLUMNS_HEADER="neogeo/assets/generated/doom_wall_columns.h"
if [ ! -f "$WALL_COLUMNS_HEADER" ] || \
   [ tools/gen_neogeo_wall_columns.py -nt "$WALL_COLUMNS_HEADER" ] || \
   [ tools/gen_neogeo_sprite_defs.py -nt "$WALL_COLUMNS_HEADER" ] || \
   [ doom64ng.h -nt "$WALL_COLUMNS_HEADER" ]
then
  python3 -B tools/gen_neogeo_wall_columns.py \
    --embedded-wad-header doom64ng.h \
    --output "$WALL_COLUMNS_HEADER"
fi

AUDIO_ASSET_DIR="neogeo/assets/generated/audio"
AUDIO_VROM_BYTES="${AUDIO_VROM_BYTES:-16777216}"
AUDIO_REPORT="$AUDIO_ASSET_DIR/doom_audio_report.txt"

mkdir -p "$AUDIO_ASSET_DIR"

audio_needs_rebuild=false
for asset in doom_audio.vrom doom_audio_generated.inc doom_audio_generated.h doom_audio_report.txt
do
  if [ ! -f "$AUDIO_ASSET_DIR/$asset" ]
  then
    audio_needs_rebuild=true
  fi
done

if [ "$audio_needs_rebuild" = false ] && \
   { [ tools/gen_neogeo_audio.py -nt "$AUDIO_REPORT" ] || \
     [ DOOM64TB.WAD -nt "$AUDIO_REPORT" ]; }
then
  audio_needs_rebuild=true
fi

if [ "$audio_needs_rebuild" = true ]
then
  python3 tools/gen_neogeo_audio.py \
    --iwad DOOM64TB.WAD \
    --out-dir "$AUDIO_ASSET_DIR" \
    --vrom-size "$AUDIO_VROM_BYTES"
fi

z80-neogeo-ihx-sdasz80 -g -p -u \
  -I"$AUDIO_ASSET_DIR" \
  -o "$AUDIO_ASSET_DIR/doomsnd.rel" \
  neogeo/sound/doomsnd.s
z80-neogeo-ihx-sdldz80 -i \
  "$AUDIO_ASSET_DIR/doomsnd.ihx" \
  "$AUDIO_ASSET_DIR/doomsnd.rel"

unset CFLAGS


CROM_FILE_BYTES="${CROM_FILE_BYTES:-4194304}"

export RENDER_OPTIONS="-DFLAT_SPAN -DFLAT_NUKAGE1_COLOR=104 -DWAD_FILE=\"DOOM64TB.WAD\" -DVIEWWINDOWWIDTH=80 -DVIEWWINDOWHEIGHT=56 -DMAPWIDTH=80 -DLOW_MEMORY -D__NGDEVKIT__ -DNEOGEO_SPRITE_MICROFB -DNEOGEO_ROM_SPRITE_DEFS -DNEOGEO_ROM_SECTOR_LINES -DNEOGEO_ROM_WALL_COLUMNS -DNEOGEO_COMPACT_BLOCKMAP -DNEOGEO_COMPACT_SECTORS -DNEOGEO_COMPACT_LINESTATE -DNEOGEO_COMPACT_MSECNODES -DNEOGEO_HEAP_SIZE=44000 ${EXTRA_RENDER_OPTIONS:-}"

export CPU=68000
m68k-neogeo-elf-gcc -c i_neogev.c $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c p_enemy2.c $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c p_map.c    $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c p_maputl.c $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c p_mobj.c   $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c p_sight.c  $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c r_data.c   $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c r_draw.c   $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -O3 -fomit-frame-pointer -flto -fwhole-program -funroll-loops -fira-loop-pressure -funsafe-loop-optimizations -freorder-blocks-algorithm=stc -fno-tree-pre
m68k-neogeo-elf-gcc -c r_plane.c  $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c tables.c   $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c w_wad.c    $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure -fno-tree-pre
m68k-neogeo-elf-gcc -c z_zone.c   $RENDER_OPTIONS -std=gnu17 -mlra -march=$CPU -Ofast -fomit-frame-pointer -fgcse-sm -flto -fwhole-program -funroll-loops -fira-loop-pressure

export CFLAGS="-std=gnu17 -mlra -march=$CPU -Oz -fomit-frame-pointer -flto -fwhole-program -fira-loop-pressure"
#export CFLAGS="$CFLAGS -Ofast -flto -fwhole-program -fomit-frame-pointer -funroll-loops -fgcse-sm -fgcse-las -fipa-pta -Wno-attributes -Wpedantic"
#export CFLAGS="$CFLAGS -Wall -Wextra"

for arg in "$@"
do
  if [ "$arg" = "-timedemo" ]
  then
    export CFLAGS="$CFLAGS -DTIMEDEMO"
    break
  fi
done

export GLOBOBJS="  am_map.c"
export GLOBOBJS+=" d_items.c"
export GLOBOBJS+=" d_main.c"
export GLOBOBJS+=" f_finale.c"
export GLOBOBJS+=" f_libt.c"
export GLOBOBJS+=" g_game.c"
export GLOBOBJS+=" hu_text.c"
export GLOBOBJS+=" i_audio.c"
export GLOBOBJS+=" i_neogeo.c"
#export GLOBOBJS+=" i_neogev.c"
export GLOBOBJS+=" i_neogev.o"
export GLOBOBJS+=" info.c"
export GLOBOBJS+=" m_cheat.c"
export GLOBOBJS+=" m_fixed.c"
export GLOBOBJS+=" m_random.c"
export GLOBOBJS+=" m_text.c"
export GLOBOBJS+=" p_doors.c"
export GLOBOBJS+=" p_enemy.c"
#export GLOBOBJS+=" p_enemy2.c"
export GLOBOBJS+=" p_enemy2.o"
export GLOBOBJS+=" p_floor.c"
export GLOBOBJS+=" p_inter.c"
export GLOBOBJS+=" p_lights.c"
#export GLOBOBJS+=" p_map.c"
export GLOBOBJS+=" p_map.o"
#export GLOBOBJS+=" p_maputl.c"
export GLOBOBJS+=" p_maputl.o"
#export GLOBOBJS+=" p_mobj.c"
export GLOBOBJS+=" p_mobj.o"
export GLOBOBJS+=" p_plats.c"
export GLOBOBJS+=" p_pspr.c"
export GLOBOBJS+=" p_setup.c"
#export GLOBOBJS+=" p_sight.c"
export GLOBOBJS+=" p_sight.o"
export GLOBOBJS+=" p_spec.c"
export GLOBOBJS+=" p_switch.c"
export GLOBOBJS+=" p_telept.c"
export GLOBOBJS+=" p_tick.c"
export GLOBOBJS+=" p_user.c"
#export GLOBOBJS+=" r_data.c"
export GLOBOBJS+=" r_data.o"
#export GLOBOBJS+=" r_draw.c"
export GLOBOBJS+=" r_draw.o"
#export GLOBOBJS+=" r_plane.c"
export GLOBOBJS+=" r_plane.o"
export GLOBOBJS+=" r_sky.c"
export GLOBOBJS+=" r_things.c"
export GLOBOBJS+=" s_sound.c"
export GLOBOBJS+=" sounds.c"
export GLOBOBJS+=" st_pal.c"
export GLOBOBJS+=" st_text.c"
#export GLOBOBJS+=" tables.c"
export GLOBOBJS+=" tables.o"
#export GLOBOBJS+=" w_wad.c"
export GLOBOBJS+=" w_wad.o"
export GLOBOBJS+=" wi_libn.c"
export GLOBOBJS+=" wi_stuff.c"
export GLOBOBJS+=" z_bmallo.c"
#export GLOBOBJS+=" z_zone.c"
export GLOBOBJS+=" z_zone.o"

m68k-neogeo-elf-gcc -specs ngdevkit -lngdevkit $GLOBOBJS $CFLAGS $RENDER_OPTIONS -o neogeo/DOOM64KB.elf -Wl,--enable-non-contiguous-regions

rm i_neogev.o
rm p_enemy2.o
rm p_map.o
rm p_maputl.o
rm p_mobj.o
rm p_sight.o
rm r_data.o
rm r_draw.o
rm r_plane.o
rm tables.o
rm w_wad.o
rm z_zone.o

m68k-neogeo-elf-objcopy -O binary -S -R .text2 --gap-fill 0xff --pad-to             1048576   neogeo/DOOM64KB.elf neogeo/rom/doom64kb-p1.p1 && dd if=neogeo/rom/doom64kb-p1.p1 of=neogeo/rom/doom64kb-p1.p1 conv=notrunc,swab status=none
m68k-neogeo-elf-objcopy -O binary    -j .text2 --gap-fill 0xff --pad-to $((0x200000+1048576)) neogeo/DOOM64KB.elf neogeo/rom/doom64kb-p2.p2 && dd if=neogeo/rom/doom64kb-p2.p2 of=neogeo/rom/doom64kb-p2.p2 conv=notrunc,swab status=none
z80-neogeo-ihx-sdobjcopy -I ihex -O binary "$AUDIO_ASSET_DIR/doomsnd.ihx" neogeo/rom/doom64kb-m1.m1 --pad-to 131072
python3 tools/gen_neogeo_color_tiles.py \
  --base-c1 neogeo/assets/base-crom-logo.c1 \
  --base-c2 neogeo/assets/base-crom-logo.c2 \
  --title-c1 neogeo/assets/doom-title.c1 \
  --title-c2 neogeo/assets/doom-title.c2 \
  --out-c1 neogeo/assets/generated/doom_color_microfb.c1 \
  --out-c2 neogeo/assets/generated/doom_color_microfb.c2 \
  --crom-size "$CROM_FILE_BYTES"
cp neogeo/assets/generated/doom_color_microfb.c1 neogeo/rom/doom64kb-c1.c1 && truncate -s "$CROM_FILE_BYTES" neogeo/rom/doom64kb-c1.c1
cp neogeo/assets/generated/doom_color_microfb.c2 neogeo/rom/doom64kb-c2.c2 && truncate -s "$CROM_FILE_BYTES" neogeo/rom/doom64kb-c2.c2
cp neogeo/assets/doom64kb.fix neogeo/rom/doom64kb-s1.s1
dd if=neogeo/assets/doom-menu.fix of=neogeo/rom/doom64kb-s1.s1 bs=32 seek=2393 conv=notrunc status=none
cp "$AUDIO_ASSET_DIR/doom_audio.vrom" neogeo/rom/doom64kb-v1.v1
truncate -s 131072 neogeo/rom/doom64kb-s1.s1
truncate -s "$AUDIO_VROM_BYTES" neogeo/rom/doom64kb-v1.v1
romtool.py -b cartridge -f zip   -p neogeo/rom/doom64kb-p1.p1 neogeo/rom/doom64kb-p2.p2 -c neogeo/rom/doom64kb-c1.c1 neogeo/rom/doom64kb-c2.c2 -v neogeo/rom/doom64kb-v1.v1 -s neogeo/rom/doom64kb-s1.s1 -m neogeo/rom/doom64kb-m1.m1 -n doom64kb -x "zip.comment="              -o neogeo/rom/doom64kb.zip
romtool.py -b hash      -f mame  -p neogeo/rom/doom64kb-p1.p1 neogeo/rom/doom64kb-p2.p2 -c neogeo/rom/doom64kb-c1.c1 neogeo/rom/doom64kb-c2.c2 -v neogeo/rom/doom64kb-v1.v1 -s neogeo/rom/doom64kb-s1.s1 -m neogeo/rom/doom64kb-m1.m1 -n doom64kb -l "Doom64KB: Neo Geo Edition" -o neogeo/rom/neogeo.xml
romtool.py -b hash      -f gngeo -p neogeo/rom/doom64kb-p1.p1 neogeo/rom/doom64kb-p2.p2 -c neogeo/rom/doom64kb-c1.c1 neogeo/rom/doom64kb-c2.c2 -v neogeo/rom/doom64kb-v1.v1 -s neogeo/rom/doom64kb-s1.s1 -m neogeo/rom/doom64kb-m1.m1 -n doom64kb -l "Doom64KB: Neo Geo Edition" -o neogeo/rom/gngeo_data.zip -x "gngeo.data=$GNGEO_DATA_FILE"
cp "$NGDEVKIT_DATA_DIR/aes.zip"    neogeo/rom/aes.zip
cp "$NGDEVKIT_DATA_DIR/neogeo.zip" neogeo/rom/neogeo.zip

for arg in "$@"
do
  if [ "$arg" = "-run" ]
  then
    ngdevkit-gngeo -b glsl --shaderpath="./neogeo/shaders" --shader="qcrt-flat.glslp" --scale 3 --no-resize --system home -i neogeo/rom -d neogeo/rom/gngeo_data.zip doom64kb --p1control A=K97,B=K115,C=K113,D=K119,START=K49,COIN=K51,UP=K82,DOWN=K81,LEFT=K80,RIGHT=K79 --p2control START=K50
    break
  fi
done
