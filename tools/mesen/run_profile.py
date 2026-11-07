#!/usr/bin/env python3
"""Run tools/mesen/profile.lua headless (Mesen 2 --testrunner), one emulator at a time.

usage: python tools/mesen/run_profile.py --out DIR [--mesen mesen] [--frames N] [--runs SPEC ...]

The Mesen folder is copied into <out>/mesen with Lua IO on and zeroed RAM (the installed copy is not touched).
Each --runs SPEC is "name:seed:players:lives:pause[:stall[:start[:passive]]]" (start = first floor 0-4; passive 1 = no attacks near the floor 4 boss), e.g. "p1:1:1:6000:900:4000" (see profile.lua for the
fields; players 0 = title and demo only). Default: one 1-player and one 2-player run.
--extra FILE ... appends Lua probes to the script (same chunk, so they see profile.lua's locals).
Results: <out>/<name>.{acc,vals,exec,args,trace,progress} and screenshots; summarise them with tools/xref.py --logs <out>.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ROM = os.path.join(ROOT, 'game.nes')


def setup(mesen_src, work):
    os.makedirs(work, exist_ok=True)
    for f in ['Mesen.exe', 'MesenCore.dll', 'libHarfBuzzSharp.dll', 'libSkiaSharp.dll', 'settings.json']:
        dst = os.path.join(work, f)
        if not os.path.exists(dst) or f == 'settings.json':
            shutil.copy2(os.path.join(mesen_src, f), dst)
    p = os.path.join(work, 'settings.json')
    s = open(p, encoding='utf-8').read()
    s = s.replace('"AllowIoOsAccess": false', '"AllowIoOsAccess": true')
    s = re.sub(r'"ScriptTimeout": \d+', '"ScriptTimeout": 100', s)
    s = s.replace('"RamPowerOnState": "Random"', '"RamPowerOnState": "AllZeros"')
    open(p, 'w', encoding='utf-8').write(s)


def lua_str(p):
    return '[[' + os.path.abspath(p) + ']]'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mesen', default=os.path.join(ROOT, 'mesen'))
    ap.add_argument('--out', required=True)
    ap.add_argument('--frames', type=int, default=12000)
    ap.add_argument('--runs', nargs='*', default=['p1:1:1:6000:900', 'p2:2:2:6000:0'])
    ap.add_argument('--no-shots', action='store_true')
    ap.add_argument('--light', action='store_true', help='no per-instruction log (faster; for --extra probes)')
    ap.add_argument('--reads', action='store_true', help='also log RAM reads (slow)')
    ap.add_argument('--extra', nargs='*', default=[], help='Lua files appended to profile.lua (they see its locals: frame, pc, seen, MT ...)')
    ap.add_argument('--labels', default=os.path.join(os.path.dirname(HERE), 'labels'))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    work = os.path.join(a.out, 'mesen')
    setup(a.mesen, work)
    rc = 0
    sys.path.insert(0, os.path.dirname(HERE))
    import xref
    entries = '' if a.light else ', '.join('0x%04X' % (0x8000 + i) for i in sorted(xref.routine_entries(xref.load_disasm(a.labels))))
    for spec in a.runs:                                  # sequential: only one emulator instance at a time
        name, seed, players, lives, pause, stall, start, passive = (spec.split(':') + ['0', '0', '0'])[:8]
        prefix = os.path.join(a.out, name)
        cfg = ('CFG = { out = %s, frames = %d, seed = %s, players = %s, lives = %s, pause = %s, stall = %s, start = %s, passive = %s, reads = %s, shots = %s, light = %s,'
               ' entries = { %s } }\n') % (lua_str(prefix), a.frames, seed, players, lives, pause, stall, start, passive, 'true' if a.reads else 'false',
                                          'false' if a.no_shots else 'true', 'true' if a.light else 'false', entries)
        script = prefix + '.lua'
        open(script, 'w').write(cfg + open(os.path.join(HERE, 'profile.lua')).read()
                                 + ''.join('\n' + open(x).read() for x in a.extra))
        for f in ('.progress', '.exec'):
            if os.path.exists(prefix + f):
                os.remove(prefix + f)
        proc = subprocess.Popen([os.path.join(work, 'Mesen.exe'), '--testrunner', os.path.abspath(ROM), script,
                                 '--timeout=3000'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:                                             # a Lua error leaves Mesen waiting for its timeout
            for _ in range(60):
                if os.path.exists(prefix + '.progress') or proc.poll() is not None:
                    break
                time.sleep(1)
            else:
                proc.kill()
                print('%s: no %s.progress after 60 s (Lua error?), Mesen killed' % (name, name))
            out = proc.communicate()[0].decode(errors='replace')
        except KeyboardInterrupt:
            proc.kill()
            raise
        ok = os.path.exists(prefix + '.exec')
        print('%s: exit %s%s' % (name, proc.returncode, '' if ok else ' (no output)'))
        if not ok:
            print(out[-2000:])
            rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
