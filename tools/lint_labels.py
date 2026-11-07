#!/usr/bin/env python3
"""Lint tools/labels/rom.txt and ram.txt.

Checks:
  - the same name used for two different addresses (ca65 would reject it)
  - one address named twice
  - addresses outside the PRG window $8000-$FFFF
  - code ranges overlapping data/words/table/text ranges (the later one silently wins in the generator)
  - module lines: names, duplicates, the first at $8000, each start on a labelled address
  - ram.txt: duplicate names / addresses, addresses outside $0000-$07FF

Exit status 1 if any problem is found.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RANGE_KINDS = ('code', 'data', 'words', 'rtswords', 'table', 'text', 'dbyt', 'numwords')
LO, HI = 0x8000, 0xFFFF


def rng(spec):
    lo, _, hi = spec.partition('-')
    return int(lo, 16), int(hi or lo, 16)


def lint_rom(path):
    errs, names, addrs, ranges, modules = [], {}, {}, [], {}
    for ln, line in enumerate(open(path, encoding='utf-8'), 1):
        parts = line.partition(';')[0].split()
        if not parts:
            continue
        w = parts[0]
        if w in RANGE_KINDS and len(parts) > 1:
            lo, hi = rng(parts[1])
            if hi < lo:
                errs.append('%d: %s range is reversed: %s' % (ln, w, parts[1]))
            if lo < LO or hi > HI:
                errs.append('%d: %s %s lies outside $8000-$FFFF' % (ln, w, parts[1]))
            for (lo2, hi2, w2, ln2) in ranges:
                if lo <= hi2 and lo2 <= hi and (w == 'code') != (w2 == 'code'):
                    errs.append('%d: %s %s overlaps %s at line %d (code vs data)' % (ln, w, parts[1], w2, ln2))
            ranges.append((lo, hi, w, ln))
            continue
        if w == 'alias':
            ends = parts[1].split('-')
            if len(parts) < 4 or not all(LO <= int(e, 16) <= HI for e in ends):
                errs.append('%d: alias needs AAAA-AAAA RAM name inside $8000-$FFFF' % ln)
            continue
        if w in ('note', 'split', 'imm', 'bitskip', 'ref'):
            for p in parts[1:3 if w in ('split', 'imm') else 2]:
                if not LO <= int(p, 16) <= HI:
                    errs.append('%d: %s address %s outside $8000-$FFFF' % (ln, w, p))
            continue
        if w == 'module' and len(parts) > 2:
            a, n = int(parts[1], 16), parts[2]
            if not LO <= a <= HI:
                errs.append('%d: module %s starts outside $8000-$FFFF' % (ln, n))
            if not re.match(r'^[a-z][a-z0-9_]*$', n):
                errs.append('%d: module name %s must be lower case (file src/prg/%s.asm)' % (ln, n, n))
            if n in modules or a in modules.values():
                errs.append('%d: module %s / $%04X defined twice' % (ln, n, a))
            modules[n] = a
            continue
        if re.match(r'^[0-9A-F]{4}$', w) and len(parts) > 1:
            a, n = int(w, 16), parts[1]
            if not LO <= a <= HI:
                errs.append('%d: %s is outside $8000-$FFFF' % (ln, w))
            if n in names and names[n][0] != a:
                errs.append('%d: name %s used for $%04X and $%04X (line %d)' % (ln, n, a, names[n][0], names[n][1]))
            if a in addrs and addrs[a][0] != n:
                errs.append('%d: $%04X named %s and %s (line %d)' % (ln, a, addrs[a][0], n, addrs[a][1]))
            names.setdefault(n, (a, ln))
            addrs.setdefault(a, (n, ln))
            continue
        errs.append('%d: cannot parse %r' % (ln, line.strip()))
    if modules and LO not in modules.values():
        errs.append('modules: the first one must start at $8000')
    for n, a in modules.items():
        if a != LO and a not in addrs:
            errs.append('module %s starts at $%04X, which has no label' % (n, a))
    return errs, set(names)


def lint_ram(path, rom_names):
    errs, names, addrs = [], {}, {}
    if not os.path.exists(path):
        return errs
    for ln, line in enumerate(open(path, encoding='utf-8'), 1):
        parts = line.partition(';')[0].split()
        if len(parts) < 2 or not re.match(r'^[0-9A-F]{4}$', parts[0]):
            continue
        a, n = int(parts[0], 16), parts[1]
        if a > 0x7FF:
            errs.append('ram %d: $%04X is outside internal RAM' % (ln, a))
        if n in names and names[n][0] != a:
            errs.append('ram %d: name %s used for $%04X and $%04X (line %d)' % (ln, n, a, names[n][0], names[n][1]))
        if a in addrs and addrs[a][0] != n:
            errs.append('ram %d: $%04X named %s and %s (line %d)' % (ln, a, addrs[a][0], n, addrs[a][1]))
        if n in rom_names:
            errs.append('ram %d: name %s is also a ROM label' % (ln, n))
        names.setdefault(n, (a, ln))
        addrs.setdefault(a, (n, ln))
    return errs


def main():
    d = os.path.join(ROOT, 'tools', 'labels')
    errs, rom_names = lint_rom(os.path.join(d, 'rom.txt'))
    errs += lint_ram(os.path.join(d, 'ram.txt'), rom_names)
    for e in errs:
        print(e)
    print('%d problem(s)' % len(errs))
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
