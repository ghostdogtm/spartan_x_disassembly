#!/usr/bin/env python3
"""Relocation test: link the PRG at $8000-SHIFT instead of $8000 and check that the result differs from the
original ROM only in operand bytes and .word/lo-hi table entries that hold ROM addresses.

  python tools/reloc_test.py [--shift 0x100]

If something else changed, or an operand that points into the ROM did not move, an address is still hard-coded
as a number in src/prg/*.asm - inserting or removing bytes there (a hack, a translation) would break the game.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import disasm as d  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shift', type=lambda x: int(x, 0), default=0x100)
    ap.add_argument('--root', default=ROOT, help='directory with config.cfg and src/')
    ap.add_argument('--rom', default=os.path.join(ROOT, d.ROM_NAME))
    ap.add_argument('--cdl', default=os.path.join(ROOT, d.CDL_NAME))
    ap.add_argument('--labels', default=os.path.join(HERE, 'labels'))
    a = ap.parse_args()
    cfg = open(os.path.join(a.root, 'config.cfg'), encoding='utf-8').read()
    cfg = cfg.replace('PRG: start = $%04X' % d.BASE, 'PRG: start = $%04X' % (d.BASE - a.shift))
    tmp_cfg, tmp_o, tmp_nes = (os.path.join(a.root, n) for n in ('config_reloc.cfg', 'src/main_reloc.o', 'reloc.nes'))
    open(tmp_cfg, 'w', encoding='utf-8').write(cfg)
    try:
        subprocess.check_call(['ca65', 'src/main.asm', '-o', 'src/main_reloc.o'], cwd=a.root)
        subprocess.check_call(['ld65', '-C', 'config_reloc.cfg', '-o', 'reloc.nes', 'src/main_reloc.o'], cwd=a.root)
        new = open(tmp_nes, 'rb').read()
    finally:
        for p in (tmp_cfg, tmp_o, tmp_nes):
            if os.path.exists(p):
                os.remove(p)
    old = d.load_rom(a.rom)
    D = d.Disasm(a.rom, a.cdl, os.path.join(a.labels, 'ram.txt'), os.path.join(a.labels, 'rom.txt'))
    D.run()
    expect = set()                                  # bytes that must move: operands / table entries pointing into ROM
    for i in D.instrs():
        mn, mode, n = D.decode(i)
        if mode in ('abs', 'abx', 'aby', 'ind') and d.rom_idx(D.operand(i, n)) is not None:
            expect.update((i + 1, i + 2))
        elif mode == 'rel' or i in D.immref:
            expect.add(i + 1)                       # may change (branch offsets stay, immediates move)
    for j in D.word_start:
        expect.update((j, j + 1))
    expect.update(D.split)
    base = 16 + d.PAD
    bad, moved, stuck = [], 0, []
    for i in range(d.SIZE):
        if old[base + i] != new[base + i]:
            if i in expect:
                moved += 1
            else:
                bad.append(i)
    for i in D.instrs():                            # high bytes of ROM pointers must change with a $100 shift
        mn, mode, n = D.decode(i)
        if mode in ('abs', 'abx', 'aby', 'ind') and d.rom_idx(D.operand(i, n)) is not None and a.shift & 0xFF00:
            if old[base + i + 2] == new[base + i + 2]:
                stuck.append(i)
    print('shift $%X: %d byte(s) moved as expected, %d unexpected change(s), %d pointer(s) that did not move'
          % (a.shift, moved, len(bad), len(stuck)))
    for i in bad[:40]:
        print('  unexpected change at $%04X' % d.cpu(i))
    for i in stuck[:40]:
        print('  operand not relocated at $%04X' % d.cpu(i))
    sys.exit(1 if bad or stuck else 0)


if __name__ == '__main__':
    main()
