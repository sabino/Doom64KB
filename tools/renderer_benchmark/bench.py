#!/usr/bin/env python3
"""Isolated GnGeo/emudbg renderer benchmark. Never connects to an existing emulator."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parent


class RSP:
    def __init__(self, sock):
        self.sock = sock
        sock.settimeout(180)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def byte(self):
        b = self.sock.recv(1)
        if not b:
            raise EOFError('emulator disconnected')
        return b

    def cmd(self, payload):
        data = payload.encode()
        self.sock.sendall(b'$' + data + b'#' + f'{sum(data) & 255:02x}'.encode())
        while self.byte() != b'$':
            pass
        data = b''
        while (b := self.byte()) != b'#':
            data += b
        checksum = self.byte() + self.byte()
        assert int(checksum, 16) == sum(data) & 255
        self.sock.sendall(b'+')
        return data.decode()

    def reg(self, n, value=None):
        if value is None:
            return int(self.cmd(f'p{n:x}'), 16)
        assert self.cmd(f'P{n:x}={value:08x}') == 'OK'

    def read(self, addr, size):
        result = b''
        for offset in range(0, size, 256):
            chunk = min(256, size - offset)
            part = bytes.fromhex(self.cmd(f'm{addr+offset:x},{chunk:x}'))
            assert len(part) == chunk
            result += part
        return result

    def write(self, addr, data):
        for offset in range(0, len(data), 256):
            part = data[offset:offset+256]
            assert self.cmd(f'M{addr+offset:x},{len(part):x}:{part.hex()}') == 'OK'

    def bp(self, addr, add=True):
        assert self.cmd(f'{"Z" if add else "z"}0,{addr:x},2') == 'OK'

    def cycles(self):
        return self.reg(18) << 32 | self.reg(19)

    def irq_cycles(self):
        return self.reg(20) << 32 | self.reg(21)

    def call(self, addr, *args):
        saved = [self.reg(n) for n in range(18)]
        sp = saved[15] - 4 * (len(args) + 1)
        sentinel = 0xffff00  # Never executed: emudbg checks PC before stepping.
        data = b''.join(n.to_bytes(4, 'big') for n in (sentinel, *args))
        self.write(sp, data)
        self.reg(15, sp)
        self.reg(17, addr)
        self.bp(sentinel)
        start = self.cycles()
        irq_start = self.irq_cycles()
        assert self.cmd('c') == 'S05'
        elapsed = self.cycles() - start
        irq_elapsed = self.irq_cycles() - irq_start
        assert self.reg(17) == sentinel
        assert self.reg(15) == sp + 4, 'callee did not return with expected SP'
        self.bp(sentinel, False)
        for n, value in enumerate(saved):
            self.reg(n, value)
        return elapsed, irq_elapsed


def symbols(elf, nm):
    found = {}
    for line in subprocess.check_output([nm, '-S', str(elf)], text=True).splitlines():
        fields = line.split()
        if len(fields) == 4:
            addr, size, _, name = fields
            found[name] = (int(addr, 16), int(size, 16))
    return found


def run(args):
    import shutil
    binary = Path(shutil.which(args.emulator) or args.emulator).resolve(strict=True)
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=False)
    # Snapshot inputs before launch; benchmark never modifies the source trees.
    shutil.copy2(Path(args.neogeo) / 'DOOM64KB.elf', out / 'game.elf')
    shutil.copytree(Path(args.neogeo) / 'rom', out / 'rom')
    manifest = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(out.rglob('*')) if p.is_file()}
    manifest['emulator'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    sym = symbols(out / 'game.elf', args.nm)

    def resolve(name):
        keys = [k for k in sym if k == name or k.startswith(name + '.')]
        assert len(keys) == 1, (name, keys)
        return sym[keys[0]]

    results = []
    for game_map in args.maps:
        for mode, width, height in [('Low', 40, 28), ('Mid', 53, 37), ('High', 80, 56)]:
            if mode not in args.modes:
                continue
            for repeat in range(args.repeats):
                tag = f'E1M{game_map}-{mode}-{repeat}'
                home = out / tag
                home.mkdir()
                # Refuse an occupied port; do not probe-connect to anyone else's process.
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(('127.0.0.1', 2159))
                env = dict(os.environ, HOME=str(home), TMPDIR=str(home), SDL_AUDIODRIVER='dummy',
                           SDL_RENDER_DRIVER='software')
                env.pop('WAYLAND_DISPLAY', None)
                command = ['xvfb-run', '-a', '-s', '-screen 0 640x480x24 -nolisten tcp',
                           str(binary), '--debug', '--68kclock=0', '--z80clock=0',
                           '--no-pal', '--no-raster', '--no-sound', '--no-joystick',
                           '--no-autoframeskip', '--no-vsync', '--no-fullscreen',
                           '-b', 'soft', '--scale=1', '--system=home', '--country=usa',
                           '-i', str(out / 'rom'), '-d', str(out / 'rom/gngeo_data.zip'),
                           'doom64kb']
                (home / 'command.json').write_text(json.dumps(command, indent=2))
                with (home / 'emulator.log').open('w') as log:
                    process = subprocess.Popen(command, cwd=home, env=env, stdout=log,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                    sock = None
                    try:
                        for _ in range(200):
                            if process.poll() is not None:
                                raise RuntimeError(f'emulator exited: {home / "emulator.log"}')
                            # Only our child was launched after the bind check.
                            try:
                                sock = socket.create_connection(('127.0.0.1', 2159), timeout=.2)
                                break
                            except ConnectionRefusedError:
                                time.sleep(.05)
                        if sock is None:
                            raise TimeoutError('owned emulator did not listen')
                        rsp = RSP(sock)
                        entry = resolve('G_Ticker')[0]
                        rsp.bp(entry)
                        assert rsp.cmd('c') == 'S05'
                        assert rsp.reg(17) == entry
                        rsp.bp(entry, False)
                        assert rsp.cycles() > 0 and rsp.irq_cycles() > 0, 'emulator-perf.patch required'
                        print(tag, 'booted', flush=True)

                        def setvar(name, value):
                            addr, size = resolve(name)
                            rsp.write(addr, value.to_bytes(size, 'big'))

                        for name in ['_g_demoplayback', '_g_gameaction', '_g_menuactive']:
                            setvar(name, 0)
                        setvar('_g_gametic', 1)
                        setvar('_g_basetic', 0)
                        rsp.call(resolve('G_InitNew')[0], 2, game_map)
                        rsp.call(resolve('G_Ticker')[0])
                        setvar('_g_gametic', 2)
                        setvar('_s_pending_microfb_mode_index', ['Low', 'Mid', 'High'].index(mode))
                        rsp.call(resolve('NG_ApplyMicroFramebufferMode')[0], ['Low', 'Mid', 'High'].index(mode))
                        actual = tuple(rsp.read(resolve(n)[0], 1)[0] for n in
                                       ['render_viewwidth', 'render_viewheight'])
                        assert actual == (width, height), actual
                        assert int.from_bytes(rsp.read(*resolve('_g_gamemap')), 'big') == game_map
                        player_mobj = int.from_bytes(rsp.read(resolve('_g_player')[0], 4), 'big')
                        assert 0x100000 <= player_mobj < 0x10ff00
                        # mobj_t.angle offset verified in p_mobj.h and renderer disassembly.
                        angle_addr = player_mobj + args.mobj_angle_offset
                        spawn_angle = int.from_bytes(rsp.read(angle_addr, 4), 'big')
                        for yaw in args.angles:
                            angle = (spawn_angle + yaw * (1 << 32) // 360) & 0xffffffff
                            rsp.write(angle_addr, angle.to_bytes(4, 'big'))
                            for sample in range(args.samples):
                                elapsed, irq = rsp.call(resolve('R_RenderPlayerView')[0], resolve('_g_player')[0])
                                raw = rsp.read(*resolve('_s_screen'))
                                pixels = bytes(raw[x * 56 + y] for y in range(height) for x in range(width))
                                assert len(set(pixels)) > 1, 'blank framebuffer'
                                (home / f'{yaw}-{sample}.pixels').write_bytes(pixels)
                                (home / f'{yaw}-{sample}.framebuffer').write_bytes(raw)
                                pose = {n: rsp.read(*resolve(n)).hex() for n in
                                        ['viewx', 'viewy', 'viewz', 'viewangle', '_g_gametic']}
                                assert int(pose['viewangle'], 16) == angle
                                row = dict(map=game_map, mode=mode, yaw=yaw, repeat=repeat, sample=sample,
                                           width=width, height=height, cycles=elapsed - irq,
                                           inclusive_cycles=elapsed, irq_cycles=irq,
                                           ms_at_12mhz=(elapsed - irq) / 12000, pose=pose,
                                           sha256=hashlib.sha256(pixels).hexdigest(),
                                           framebuffer_sha256=hashlib.sha256(raw).hexdigest())
                                results.append(row)
                                (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
                                print(json.dumps(row), flush=True)
                    finally:
                        if sock:
                            sock.close()
                        # Only the process group created above; never use pkill or services.
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                        # xvfb-run may exit before its child has released the debugger socket.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        time.sleep(.2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--neogeo', required=True)
    parser.add_argument('--emulator', default=str(ROOT / 'build-gngeo/src/gngeo'))
    parser.add_argument('--nm', default='m68k-neogeo-elf-nm')
    parser.add_argument('--output', required=True)
    parser.add_argument('--maps', nargs='+', type=int, default=[1, 2], choices=[1, 2])
    parser.add_argument('--modes', nargs='+', default=['Low', 'Mid', 'High'], choices=['Low', 'Mid', 'High'])
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--angles', type=int, nargs='+', default=[0, 90, 180, 270])
    parser.add_argument('--mobj-angle-offset', type=int, default=32)
    run(parser.parse_args())
