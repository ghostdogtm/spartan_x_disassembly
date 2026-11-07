#!/usr/bin/env python3
"""Cross-reference report for naming: who calls each routine and which routines read/write each RAM byte.

  python tools/xref.py [--logs DIR] [--out docs/xref.txt] [--unnamed]

Static part (from the disassembly, same inputs as tools/disasm.py):
  routines  entry, size, callers, callees, RAM read/written (direct operands only)
  RAM       per address: routines that write (W), read (R) or modify (M) it
Dynamic part (--logs: output of tools/mesen/run_profile.py, every <run>.acc/.vals/.exec in DIR is merged):
  routines  executed instruction count, game modes (gameMode values) in which they ran, A/X/Y values on entry
  RAM       values written (min, max, number of distinct values, first values) and routines that touched the byte
            only through an index register (e.g. formation,X) - invisible in the static part
--unnamed lists only routines with auto names (sub_/loc_) and RAM without a name in ram.txt.
"""
import argparse
import glob
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import disasm as d  # noqa: E402

WRITE = {'STA', 'STX', 'STY'}
MODIFY = {'INC', 'DEC', 'ASL', 'LSR', 'ROL', 'ROR'}
STACK = {'PHA', 'PLA', 'PHP', 'PLP', 'JSR', 'RTS', 'RTI', 'BRK'}
AUTO = ('sub_', 'loc_', 'dat_', 'tbl_')


def load_logs(path):
    acc = defaultdict(lambda: defaultdict(int))     # (kind, addr) -> pc -> count
    vals = {}
    execn, modes = defaultdict(int), defaultdict(int)
    for f in glob.glob(os.path.join(path, '*.acc')):
        for line in open(f):
            k, pc, a, n = line.split()
            acc[(k, int(a, 16))][int(pc, 16)] += int(n)
    for f in glob.glob(os.path.join(path, '*.vals')):
        for line in open(f):
            p = line.split()
            a, n, chg, lo, hi, nd = int(p[0], 16), int(p[1]), int(p[2]), int(p[3], 16), int(p[4], 16), int(p[5])
            v = set(p[6:])
            if a in vals:
                o = vals[a]
                vals[a] = (o[0] + n, o[1] + chg, min(o[2], lo), max(o[3], hi), max(o[4], nd), o[5] | v)
            else:
                vals[a] = (n, chg, lo, hi, nd, v)
    for f in glob.glob(os.path.join(path, '*.exec')):
        for line in open(f):
            pc, n, m = line.split()
            execn[int(pc, 16)] += int(n)
            modes[int(pc, 16)] |= int(m, 16)
    args = defaultdict(lambda: {'A': set(), 'X': set(), 'Y': set()})
    for f in glob.glob(os.path.join(path, '*.args')):
        for line in open(f):
            p = line.split()
            for part in p[1:]:
                reg, rest = part.split('=')
                args[int(p[0], 16)][reg] |= {int(v, 16) for v in rest.split(':')[1].split(',') if v}
    return acc, vals, execn, modes, args


def values_str(s):
    v = sorted(s)
    if len(v) <= 12:
        return ' '.join('%02X' % x for x in v)
    return '%d values %02X..%02X' % (len(v), v[0], v[-1])


def routine_entries(D):
    """PRG indexes of routine entries: JSR targets, CDL subroutine starts, jump-table handlers, named code"""
    entries = set()
    for i, kinds in D.refs.items():
        if D.kind[i] == d.K_OP and ('sub' in kinds or 'ptr' in kinds or (D.cdl[i] & d.CDL_SUB)):
            entries.add(i)
    for i in D.user['labels']:
        if D.kind[i] == d.K_OP:
            entries.add(i)
    return entries


def load_disasm(labels):
    D = d.Disasm(os.path.join(d.ROOT, d.ROM_NAME), os.path.join(d.ROOT, d.CDL_NAME),
                 os.path.join(labels, 'ram.txt'), os.path.join(labels, 'rom.txt'))
    D.run()
    D.ram_used_direct = {x for x in D.ram_used if x - 1 not in D.ptrs or x in D.ram_user}
    return D


def mode_str(m):
    return ','.join(str(b) for b in range(16) if m >> b & 1) or '-'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--labels', default=os.path.join(HERE, 'labels'))
    ap.add_argument('--logs')
    ap.add_argument('--out', default=os.path.join(d.ROOT, 'docs', 'xref.txt'))
    ap.add_argument('--unnamed', action='store_true')
    a = ap.parse_args()
    D = load_disasm(a.labels)
    entries = routine_entries(D)
    order = sorted(entries)
    owner_of = {}
    cur = None
    for i in range(d.SIZE):
        if i in entries:
            cur = i
        owner_of[i] = cur
    name = lambda i: D.labels.get(i, '$%04X' % d.cpu(i)) if i is not None else '?'

    callers, callees = defaultdict(set), defaultdict(set)
    dispatched = defaultdict(set)
    ram_rw = defaultdict(lambda: defaultdict(set))   # addr -> 'R'/'W'/'M' -> owners
    sub_ram = defaultdict(lambda: defaultdict(set))  # owner -> 'R'/'W'/'M' -> addrs
    end_of = {}
    for i in D.instrs():
        mn, mode, n = D.decode(i)
        o = owner_of[i]
        end_of[o] = i + n
        if mode in ('abs', 'abx', 'aby', 'zp', 'zpx', 'zpy', 'izx', 'izy', 'ind'):
            v = D.operand(i, n)
            t = d.rom_idx(v)
            if t is not None:
                if mn == 'JSR' or (mn == 'JMP' and t in entries and owner_of[t] != o):
                    callers[t].add(o)
                    callees[o].add(t)
            elif v < 0x800:
                k = 'W' if mn in WRITE else 'M' if mn in MODIFY and mode != 'acc' else 'R'
                ram_rw[v][k].add(o)
                sub_ram[o][k].add(v)
                if mode in ('izx', 'izy', 'ind'):
                    ram_rw[v + 1]['R'].add(o)
    for j in D.word_start:
        t = d.rom_idx(D.word(j) + D.word_adj.get(j, 0))
        if t is not None:
            dispatched[t].add(owner_of[j])

    acc, vals, execn, modes, args = load_logs(a.logs) if a.logs else ({}, {}, {}, {}, {})
    instr_start = {}
    for i in D.instrs():
        instr_start[d.cpu(i)] = i

    def ramname(x):
        return D.ram_name(x)

    def named_ram(x):
        return not D.ram_name(x).startswith(('ram_', 'ptr_'))

    out = []
    w = out.append
    w('; Cross-reference report generated by tools/xref.py%s' % (' (with Mesen logs)' if a.logs else ''))
    w('; modes = gameMode values in which the code ran: 0 intro, 1 title, 2 demo, 3 game, 4/5 ?')
    w('')
    w('=' * 100)
    w('ROUTINES')
    w('=' * 100)
    for e in order:
        nm = name(e)
        if a.unnamed and not nm.startswith(AUTO):
            continue
        size = end_of.get(e, e) - e
        ran = execn.get(d.cpu(e), 0) if execn else None
        line = '%s  $%04X  %d bytes' % (nm, d.cpu(e), size)
        if execn:
            m = 0
            for i in range(e, end_of.get(e, e)):
                m |= modes.get(d.cpu(i), 0)
            line += '  runs %d  modes %s' % (ran, mode_str(m))
        w(line)
        if d.cpu(e) in args:
            r = args[d.cpu(e)]
            w('    on entry  : A = %s | X = %s | Y = %s' % (values_str(r['A']), values_str(r['X']), values_str(r['Y'])))
        if callers.get(e):
            w('    called by : ' + ', '.join(sorted(name(c) for c in callers[e])))
        if dispatched.get(e):
            w('    table in  : ' + ', '.join(sorted(name(c) for c in dispatched[e])))
        if callees.get(e):
            w('    calls     : ' + ', '.join(sorted(name(c) for c in callees[e])))
        for k, title in (('W', 'writes'), ('M', 'modifies'), ('R', 'reads')):
            if sub_ram[e].get(k):
                w('    %-10s: %s' % (title, ', '.join(ramname(x) for x in sorted(sub_ram[e][k]))))
    w('')
    w('=' * 100)
    w('RAM')
    w('=' * 100)
    def indexed(k, x):
        """owners of the instructions that touched x through an index register / pointer (Mesen log)"""
        dyn = set()
        for pc in acc.get((k, x), {}):
            i = instr_start.get(pc)
            if i is None:
                continue
            mn, mode, nb = D.decode(i)
            if mn in STACK or (mode in ('zp', 'abs') and D.operand(i, nb) == x):
                continue
            if D.labels.get(owner_of[i]) in ('RESET', 'ColdBootInit'):
                continue                                    # RAM clear / init loops touch everything
            dyn.add(name(owner_of[i]))
        return dyn

    # a byte touched only by the RAM clear loops is not interesting: keep the ones that something reads
    addrs = sorted(set(ram_rw) | ({x for (k, x) in acc if k == 'R' and indexed('R', x)} if acc else set()))
    for x in addrs:
        if 0x100 <= x < 0x200 and x > 0x12F and x not in D.ram_user:
            continue                                        # stack
        if a.unnamed and named_ram(x):
            continue
        line = '%-20s $%03X' % (ramname(x), x)
        if x in vals:
            n, chg, lo, hi, nd, v = vals[x]
            line += '  writes %d changes %d  values %02X..%02X (%d): %s' % (n, chg, lo, hi, nd, ' '.join(sorted(v)[:16]))
        w(line)
        for k, title in (('W', 'written by'), ('M', 'modified by'), ('R', 'read by')):
            if ram_rw[x].get(k):
                w('    %-12s: %s' % (title, ', '.join(sorted(name(o) for o in ram_rw[x][k]))))
        if acc:
            for k, title in (('W', 'W indexed'), ('R', 'R indexed')):
                dyn = indexed(k, x)
                if dyn:
                    w('    %-12s: %s' % (title, ', '.join(sorted(dyn))))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    print('%d routines, %d RAM addresses -> %s' % (len(order), len(addrs), a.out))


if __name__ == '__main__':
    main()
