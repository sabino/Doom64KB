if "%WATCOM%" == "" goto error

mkdir WC

set CFLAGS=-5r -oneatx -zp4 -mf -bcl=dos4g -q -wx -za99

set CFLAGS=%CFLAGS% -DFLAT_SPAN -DWAD_FILE="DOOM64KL.WAD" -DMAP_WAD_FILE="DOOM64ML.WAD" -DVIEWWINDOWWIDTH=240 -DINSTRUMENTED -DC_ONLY




@set GLOBOBJS=
@set GLOBOBJS=%GLOBOBJS% am_map.c
@set GLOBOBJS=%GLOBOBJS% d_items.c
@set GLOBOBJS=%GLOBOBJS% d_main.c
@set GLOBOBJS=%GLOBOBJS% f_finale.c
@set GLOBOBJS=%GLOBOBJS% f_lib.c
@set GLOBOBJS=%GLOBOBJS% g_game.c
@set GLOBOBJS=%GLOBOBJS% hu_stuff.c
@set GLOBOBJS=%GLOBOBJS% i_audio.c
@set GLOBOBJS=%GLOBOBJS% i_ibm.c
@set GLOBOBJS=%GLOBOBJS% i_vvga13.c
@set GLOBOBJS=%GLOBOBJS% info.c
@set GLOBOBJS=%GLOBOBJS% m_cheat.c
@set GLOBOBJS=%GLOBOBJS% m_fixed.c
@set GLOBOBJS=%GLOBOBJS% m_menu.c
@set GLOBOBJS=%GLOBOBJS% m_random.c
@set GLOBOBJS=%GLOBOBJS% p_doors.c
@set GLOBOBJS=%GLOBOBJS% p_enemy.c
@set GLOBOBJS=%GLOBOBJS% p_enemy2.c
@set GLOBOBJS=%GLOBOBJS% p_floor.c
@set GLOBOBJS=%GLOBOBJS% p_inter.c
@set GLOBOBJS=%GLOBOBJS% p_lights.c
@set GLOBOBJS=%GLOBOBJS% p_map.c
@set GLOBOBJS=%GLOBOBJS% p_maputl.c
@set GLOBOBJS=%GLOBOBJS% p_mobj.c
@set GLOBOBJS=%GLOBOBJS% p_plats.c
@set GLOBOBJS=%GLOBOBJS% p_pspr.c
@set GLOBOBJS=%GLOBOBJS% p_setup.c
@set GLOBOBJS=%GLOBOBJS% p_sight.c
@set GLOBOBJS=%GLOBOBJS% p_spec.c
@set GLOBOBJS=%GLOBOBJS% p_switch.c
@set GLOBOBJS=%GLOBOBJS% p_telept.c
@set GLOBOBJS=%GLOBOBJS% p_tick.c
@set GLOBOBJS=%GLOBOBJS% p_user.c
@set GLOBOBJS=%GLOBOBJS% r_data.c
@set GLOBOBJS=%GLOBOBJS% r_draw.c
@set GLOBOBJS=%GLOBOBJS% r_plane.c
@set GLOBOBJS=%GLOBOBJS% r_sky.c
@set GLOBOBJS=%GLOBOBJS% r_things.c
@set GLOBOBJS=%GLOBOBJS% s_sound.c
@set GLOBOBJS=%GLOBOBJS% sounds.c
@set GLOBOBJS=%GLOBOBJS% st_pal.c
@set GLOBOBJS=%GLOBOBJS% st_stuff.c
@set GLOBOBJS=%GLOBOBJS% tables.c
@set GLOBOBJS=%GLOBOBJS% v_video.c
@set GLOBOBJS=%GLOBOBJS% w_wad.c
@set GLOBOBJS=%GLOBOBJS% wi_lib.c
@set GLOBOBJS=%GLOBOBJS% wi_stuff.c
@set GLOBOBJS=%GLOBOBJS% z_bmallo.c
@set GLOBOBJS=%GLOBOBJS% z_zone.c

wcl386 %GLOBOBJS% %CFLAGS% -fe=WC/WC32M13H.EXE -fm=WC/WC32M13H.MAP
del *.obj
del *.err

goto end

:error
@echo Set the environment variables before running this script!

:end
