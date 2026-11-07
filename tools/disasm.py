#!/usr/bin/env python3
"""Spartan X (J) - CDL driven disassembler producing ca65 sources.

  python tools/disasm.py [--rom ROM] [--cdl CDL] [--out DIR] [--labels DIR] [--build] [--no-chr]

The ROM is the original dump: iNES header + 32KB PRG (CPU $8000-$FFFF, NROM-256) + 8KB CHR. PAD > 0 would put
$FF padding in front of the PRG (an 8KB game in an NROM-128 image); load_rom() adds that padding when the dump does
not have it, compare_roms() removes it again for the check. Here PAD = 0.

Inputs : ROM (.nes), CDL (Mesen2 'CDLv2'), tools/labels/ram.txt, tools/labels/rom.txt
Outputs: src/prg/*.asm (one file per rom.txt module), src/main.asm, src/chr.asm, src/defines.inc, src/ram.inc, config.cfg, header.bin,
         assemble.bat, compare.bat, docs/coverage.txt, game.mlb (labels for Mesen)
CHR tiles are split by tools/chr_tool.py (run automatically unless --no-chr); src/chr.asm with the game data
kept in CHR ROM is written by tools/chrdata.py.
**Overwrites src/** - after editing src/*.asm by hand run it only with --out DIR.
"""
import argparse
import bisect
import glob
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opcodes import OPS, BRANCHES  # noqa: E402
import chrdata  # noqa: E402

BASE = 0x8000           # CPU address of the real PRG
SIZE = 0x8000           # 32KB (NROM-256)
PAD = 0                 # $FF padding in front of the PRG in the assembled ROM (none)
CHR_SIZE = 0x2000       # 8KB CHR ROM
AUTO_IMM_PAIRS = False   # guess #<label/#>label pairs; in Spartan X every guess was a coincidence (use 'imm')
UNREF_MIN = 6           # unreferenced word tables need at least this many words pointing at code
FILL_MIN = 8            # identical-byte runs of at least this length become .res
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ROM_NAME = 'Spartan X (J) [!].nes'
CDL_NAME = 'Spartan X (J) [!].cdl'
OUT_NAME = 'game'

CDL_CODE, CDL_DATA, CDL_JUMP, CDL_SUB = 1, 2, 4, 8     # Mesen2 NES CDL flags
K_UNK, K_OP, K_ARG, K_DATA, K_WORD = 0, 1, 2, 3, 4      # byte kinds

HW_REGS = {0x2000: 'PPUCTRL', 0x2001: 'PPUMASK', 0x2002: 'PPUSTATUS', 0x2003: 'OAMADDR',
           0x2004: 'OAMDATA', 0x2005: 'PPUSCROLL', 0x2006: 'PPUADDR', 0x2007: 'PPUDATA',
           0x4000: 'SQ1_VOL', 0x4001: 'SQ1_SWEEP', 0x4002: 'SQ1_LO', 0x4003: 'SQ1_HI',
           0x4004: 'SQ2_VOL', 0x4005: 'SQ2_SWEEP', 0x4006: 'SQ2_LO', 0x4007: 'SQ2_HI',
           0x4008: 'TRI_LINEAR', 0x4009: 'APU_UNUSED1', 0x400A: 'TRI_LO', 0x400B: 'TRI_HI',
           0x400C: 'NOISE_VOL', 0x400D: 'APU_UNUSED2', 0x400E: 'NOISE_LO', 0x400F: 'NOISE_HI',
           0x4010: 'DMC_FREQ', 0x4011: 'DMC_RAW', 0x4012: 'DMC_START', 0x4013: 'DMC_LEN',
           0x4014: 'OAMDMA', 0x4015: 'SNDCHN', 0x4016: 'JOY1', 0x4017: 'JOY2'}
RESERVED = {o[0].lower() for o in OPS.values()} | {'a', 'x', 'y', 's', 'z'}
NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def cpu(i):
    return BASE + i


def rom_idx(a):
    """CPU address -> index into the PRG, or None outside $8000-$FFFF"""
    return a - BASE if BASE <= a <= 0xFFFF else None

def load_consts(path):
    """NAME = EXPR [@var ...] [; comment]   constant (src/const.inc); @var (RAM/register name, may be name+N): used
                                           automatically where the value is stored into var (LDx #v / STx var) or
                                           tested against it (LDx var / CMP|CPx|AND|ORA|EOR #v, also later CMPs of
                                           the same LDx when only branches lie between)
       use AAAA [AAAA ...] EXPR             the immediate of these instructions is EXPR (checked against the byte)
       data AAAA EXPR[, EXPR ...] [; c]     data bytes from AAAA on are printed as these expressions (checked);
                                           the comment goes to the end of the line
       an indented comment-only line right after NAME = ... continues that constant's comment
       struct Name field[:size] ... [; c]   .struct in src/const.inc (fields are bytes unless :size)
       array var Name                       RAM var is an array of struct Name: var+N is printed as
                                           var+(N - field offset)+Name::field
       enum [; c] ... endenum               the NAME = ... lines between are printed as an anonymous .enum
                                           (names stay global; values that do not follow the previous + 1
                                           are given explicitly)"""
    r = dict(defs=[], auto=[], uses={}, data={}, datacomment={}, blocks=[], structs={}, arrays={})
    if not os.path.exists(path):
        return r
    prev_def = None
    for ln, line in enumerate(open(path, encoding='utf-8'), 1):
        code = re.sub(r"'.'", lambda m: m.group(0).replace(';', '\0'), line)
        code, _, comment = code.partition(';')
        code = code.replace('\0', ';').strip()
        if not code:
            if line[:1] in ' \t' and comment.strip() and prev_def == ln - 1:     # indented: comment continuation
                n, e, c, w = r['defs'][-1]
                r['defs'][-1] = (n, e, c + '\n' + comment.strip(), w)
                prev_def = ln
            continue
        where = '%s:%d' % (path, ln)
        parts = code.split()
        if parts[0] == 'struct':
            if len(parts) < 3:
                sys.exit('%s: struct Name field[:size] ...' % where)
            fields, off = [], 0
            for f in parts[2:]:
                fn, _, size = f.partition(':')
                fields.append((fn, off, int(size or '1', 0)))
                off += int(size or '1', 0)
            r['structs'][parts[1]] = fields
            r['blocks'].append((len(r['defs']), 'struct', parts[1], comment.strip()))
            continue
        if parts[0] == 'array':
            if len(parts) != 3 or parts[2] not in r['structs']:
                sys.exit('%s: array var StructName (struct defined above)' % where)
            r['arrays'][parts[1]] = parts[2]
            continue
        if parts[0] in ('enum', 'endenum'):
            r['blocks'].append((len(r['defs']), parts[0], None, comment.strip()))
            continue
        if parts[0] == 'data':
            m = re.match(r'data\s+(\S+)\s+(.+)$', code)
            i = rom_idx(parse_addr(m.group(1))) if m else None
            if i is None:
                sys.exit('%s: data AAAA EXPR[, EXPR ...]' % where)
            if comment.strip():
                r['datacomment'][i] = comment.strip()
            for k, e in enumerate(x.strip() for x in m.group(2).split(',')):
                if i + k in r['data']:
                    sys.exit('%s: $%04X already has a data expression' % (where, cpu(i + k)))
                r['data'][i + k] = (e, where)
            continue
        if parts[0] == 'use':
            if len(parts) < 3:
                sys.exit('%s: use AAAA [AAAA ...] EXPR' % where)
            for a in parts[1:-1]:
                i = rom_idx(parse_addr(a))
                if i is None:
                    sys.exit('%s: address %s outside $8000-$FFFF' % (where, a))
                if i in r['uses']:
                    sys.exit('%s: $%s already has a constant' % (where, a))
                r['uses'][i] = (parts[-1], where)
            continue
        name, eq, rest = code.partition('=')
        name = name.strip()
        if not eq:
            sys.exit('%s: expected NAME = EXPR or use ...' % where)
        check_name(name, where)
        words = rest.split()
        expr = ' '.join(w for w in words if not w.startswith('@'))
        r['defs'].append((name, expr, comment.strip(), where))
        prev_def = ln
        r['auto'].extend((name, w[1:], where) for w in words if w.startswith('@'))
    return r


def eval_expr(expr, names, where):
    """value of a ca65 constant expression: $hex %bin 'c' numbers, names, + - * / & | ^ ~ << >> ( ),
    a leading < or > takes the low or high byte"""
    part = None
    e = expr.strip()
    if e[:1] in '<>' and e[1:2] not in '<>':
        part, e = e[0], e[1:]
    py = re.sub(r"'(.)'", lambda m: str(ord(m.group(1))), e)
    py = re.sub(r'\$([0-9A-Fa-f]+)', r'0x\1', py)
    py = re.sub(r'%([01]+)', r'0b\1', py).replace('/', '//')
    try:
        v = eval(py, {'__builtins__': {}}, dict(names))
    except Exception as ex:
        sys.exit('%s: cannot evaluate %r (%s)' % (where, expr, ex))
    return v & 0xFF if part == '<' else (v >> 8) & 0xFF if part == '>' else v



# ----------------------------------------------------------------------------- label files
def parse_addr(s):
    return int(s.replace('$', '').replace('0x', ''), 16)


def check_name(name, where, local_ok=False):
    if local_ok and name.startswith('@'):
        name = name[1:]                                 # ca65 cheap local label
    if name.lower() in RESERVED or not NAME_RE.match(name):
        sys.exit('%s: bad name %r' % (where, name))


def load_ram_labels(path):
    """ADDR NAME [SIZE] ; comment"""
    names = {}                      # addr -> (name, size, comment)
    if not os.path.exists(path):
        return names
    for ln, line in enumerate(open(path, encoding='utf-8'), 1):
        code, _, comment = line.partition(';')
        parts = code.split()
        if not parts:
            continue
        check_name(parts[1], '%s:%d' % (path, ln))
        size = int(parts[2], 0) if len(parts) > 2 else 1
        names[parse_addr(parts[0])] = (parts[1], size, comment.strip())
    return names


def parse_range(spec):
    lo, _, hi = spec.partition('-')
    lo, hi = rom_idx(parse_addr(lo)), rom_idx(parse_addr(hi or lo))
    if lo is None or hi is None or hi < lo:
        raise ValueError('range %r outside $8000-$FFFF' % spec)
    return lo, hi


def load_rom_labels(path):
    """AAAA name ; comment                    label (CPU address $8000-$FFFF)
       code|data|words|rtswords AAAA-AAAA    force classification (rtswords: stored address = target-1)
       table AAAA-AAAA N [header]            data table, N bytes per row; references into it become name+offset
       text AAAA-AAAA                        ASCII string, emitted as .byte "..."
       split LO HI N [adj] [prefix]          lo/hi address tables of N entries (adj 1 = RTS trick)
       imm AAAA AAAA                         immediates at these two instructions are #<target / #>target
       note AAAA ; text                      comment line printed before the line at AAAA
       bitskip AAAA                          BIT abs ($2C) used to skip a 2-byte instruction: AAAA becomes a data
                                             byte and AAAA+1 the hidden instruction
       module AAAA name ; description        source file src/prg/name.asm starts at AAAA (the first one at $8000)
       dbyt AAAA-AAAA                        16-bit numbers, big-endian (hi, lo): .dbyt $XXXX
       numwords AAAA-AAAA                    16-bit numbers, little-endian, not addresses: .word $XXXX
       ref AAAA Name[+-N]                    absolute operand of the instruction at AAAA written as a ROM label
                                             +- offset (a table whose base lies inside code); checked against the byte
       alias AAAA-AAAA RAM[/N] name ; c      inside this code range the N (default 1) bytes at RAM are written as
                                             name, name+1, ...; "name = <RAM name> ; comment" is printed before AAAA
                                             (meaningful names for scratch bytes such as ptr_00 in one routine)"""
    r = dict(labels={}, force=[], notes=defaultdict(list), tables={}, texts={}, splits=[], imms=[], bitskip=[],
             modules={}, num16={}, refs={}, aliases=[])
    if not os.path.exists(path):
        return r
    for ln, line in enumerate(open(path, encoding='utf-8'), 1):
        code, _, comment = line.partition(';')
        parts = code.split()
        comment = comment.strip()
        if not parts:
            continue
        where = '%s:%d' % (path, ln)
        try:
            cmd = parts[0]
            if cmd in ('code', 'data', 'words', 'rtswords'):
                r['force'].append((cmd,) + parse_range(parts[1]))
            elif cmd == 'table':
                lo, hi = parse_range(parts[1])
                r['tables'][lo] = (int(parts[2], 0), hi, ' '.join(parts[3:]) or comment)
                r['force'].append(('data', lo, hi))
            elif cmd == 'text':
                lo, hi = parse_range(parts[1])
                r['texts'][lo] = hi
                r['force'].append(('data', lo, hi))
            elif cmd == 'split':
                lo, hi, n = rom_idx(parse_addr(parts[1])), rom_idx(parse_addr(parts[2])), int(parts[3], 0)
                adj = int(parts[4], 0) if len(parts) > 4 else 0
                r['splits'].append((lo, hi, n, adj, parts[5] if len(parts) > 5 else None))
            elif cmd == 'imm':
                r['imms'].append((rom_idx(parse_addr(parts[1])), rom_idx(parse_addr(parts[2]))))
            elif cmd in ('dbyt', 'numwords'):
                lo, hi = parse_range(parts[1])
                if (hi - lo + 1) % 2:
                    raise ValueError('odd number of bytes')
                r['force'].append(('data', lo, hi))
                for k in range(lo, hi, 2):
                    r['num16'][k] = cmd
            elif cmd == 'module':
                i = rom_idx(parse_addr(parts[1]))
                if i is None or not re.match(r'[a-z][a-z0-9_]*$', parts[2]):
                    raise ValueError('module AAAA name')
                r['modules'][i] = (parts[2], comment)
            elif cmd == 'bitskip':
                r['bitskip'].append(rom_idx(parse_addr(parts[1])))
            elif cmd == 'ref':
                m = re.match(r'([A-Za-z_]\w*)(?:([+-])(\$[0-9A-Fa-f]+|\d+))?$', parts[2])
                if not m:
                    raise ValueError('ref AAAA Name[+-N]')
                off = int(m.group(3).replace('$', '0x'), 0) if m.group(3) else 0
                r['refs'][rom_idx(parse_addr(parts[1]))] = (m.group(1), -off if m.group(2) == '-' else off, parts[2])
            elif cmd == 'alias':
                lo, hi = parse_range(parts[1])
                check_name(parts[3], where)
                ram, _, size = parts[2].partition('/')
                r['aliases'].append((lo, hi, int(ram.lstrip('$'), 16), int(size or 1), parts[3], comment))
            elif cmd == 'note':
                r['notes'][rom_idx(parse_addr(parts[1]))].append(comment)
            else:
                i = rom_idx(parse_addr(cmd))
                if i is None:
                    raise ValueError('address outside $8000-$FFFF')
                check_name(parts[1], where, local_ok=True)
                r['labels'][i] = (parts[1], comment)
        except (ValueError, IndexError) as e:
            sys.exit('%s cannot parse %r (%s)' % (where, line.strip(), e))
    return r


# ----------------------------------------------------------------------------- ROM files
def load_rom(path):
    """ROM image with the $FF padding: header + PAD + PRG + CHR"""
    d = open(path, 'rb').read()
    if len(d) == 16 + SIZE + CHR_SIZE:                    # original dump without the padding
        d = d[:16] + b'\xff' * PAD + d[16:]
    return d


def strip_padding(d):
    """the inverse of load_rom: the original dump (header + PRG + CHR); None if the padding is not all $FF"""
    if len(d) != 16 + PAD + SIZE + CHR_SIZE or (PAD and set(d[16:16 + PAD]) != {0xFF}):
        return None
    return d[:16] + d[16 + PAD:]


def compare_roms(built, original):
    """(ok, message): the assembled ROM (with padding) against the original dump (with or without it)"""
    a, b = open(built, 'rb').read(), open(original, 'rb').read()
    s = strip_padding(a)
    if s is None:
        return False, 'DIFFERENT: %s is not header + %d $FF + %d PRG + %d CHR bytes (%d bytes)' % (built, PAD, SIZE, CHR_SIZE, len(a))
    if len(b) == len(a):
        b = strip_padding(b)
        if b is None:
            return False, 'DIFFERENT: the padding of %s is not all $FF' % original
    if s == b:
        return True, 'IDENTICAL'
    diff = [k for k in range(min(len(s), len(b))) if s[k] != b[k]]
    return False, 'DIFFERENT (sizes %d/%d, %d bytes differ, first at original file offset $%X)' % (
        len(s), len(b), len(diff), diff[0] if diff else -1)


# ----------------------------------------------------------------------------- main class
class Disasm:
    def __init__(self, rom_path, cdl_path, ram_path, rom_lbl_path):
        d = load_rom(rom_path)
        if d[:4] != b'NES\x1a' or len(d) != 16 + PAD + SIZE + CHR_SIZE:
            sys.exit('%s: expected an iNES file with %dKB PRG + %dKB CHR' % (rom_path, SIZE // 1024, CHR_SIZE // 1024))
        self.header = d[:16]
        prg = d[16:16 + PAD + SIZE]
        self.pad = prg[:PAD]
        if PAD and set(self.pad) != {0xFF}:
            print('WARNING: the PRG padding is not all $FF; it is emitted as raw bytes')
        self.rom = prg[PAD:]
        self.chr = d[16 + PAD + SIZE:16 + PAD + SIZE + CHR_SIZE]
        c = open(cdl_path, 'rb').read()
        if c[:5] == b'CDLv2':
            c = c[9:]                                   # 'CDLv2' + CRC32 of the ROM
        self.cdl = c[PAD:PAD + SIZE]
        self.kind = bytearray(SIZE)
        self.traced = set()         # instruction starts found by static tracing (not in the CDL)
        self.anomalies = []
        self.user = load_rom_labels(rom_lbl_path)
        self.ram_user = load_ram_labels(ram_path)
        self.consts = load_consts(os.path.join(os.path.dirname(rom_lbl_path), 'const.txt'))
        self.immconst = {}          # instruction idx -> constant expression of its immediate
        self.dataexpr = {}          # data byte idx -> expression (const.txt "data")
        self.chr_labels = {}        # RAM addr -> (name, size, comment) of labels defined in src/chr.asm
        self.chr_asm = ''
        self.chr_names = set()
        self.locked = set()         # forced data: never turned into word tables
        self.word_start = set()
        self.word_adj = {}          # word start -> 1 for RTS-trick tables
        self.table_log = []
        self.table_targets = set()
        self.split = {}             # idx -> ('<'|'>', target idx, adj)
        self.split_ram = {}         # idx -> ('<'|'>', RAM address, adj): split table entries pointing to RAM
        self.immref = {}            # instruction idx -> ('<'|'>', target idx)
        self.refs = defaultdict(set)
        self.labels = {}            # idx -> name
        self.local_scope = {}       # idx of a cheap local label (@name) -> its scope label idx
        self.raw = []               # (idx, text) operands left as raw numbers
        self.ram_used = defaultdict(int)
        self.ptrs = set()           # zp addresses used as (zp),Y / (zp,X) pointers
        self.row_break = set()

    # ------------------------------------------------------------------ decoding
    def rel_dest(self, i):
        v = self.rom[i + 1]
        t = i + 2 + (v - 256 if v & 0x80 else v)
        return t if 0 <= t < SIZE else None

    def decode(self, i):
        op = self.rom[i]
        if op not in OPS:
            return None
        mn, mode, n = OPS[op]
        if i + n > SIZE or (mode == 'rel' and self.rel_dest(i) is None):
            return None
        return mn, mode, n

    def operand(self, i, n):
        return self.rom[i + 1] if n == 2 else self.rom[i + 1] | (self.rom[i + 2] << 8)

    def word(self, i):
        return self.rom[i] | (self.rom[i + 1] << 8)

    def set_instr(self, i, n):
        self.kind[i] = K_OP
        for j in range(1, n):
            self.kind[i + j] = K_ARG

    # ------------------------------------------------------------------ CDL + forced ranges
    def classify_cdl(self):
        i = 0
        while i < SIZE:
            f = self.cdl[i]
            if f & CDL_CODE:
                d = self.decode(i)
                if d and all(self.cdl[i + j] & CDL_CODE for j in range(1, d[2])):
                    self.set_instr(i, d[2])
                    i += d[2]
                    continue
                self.anomalies.append(i)
                self.kind[i] = K_DATA
            elif f & CDL_DATA:
                self.kind[i] = K_DATA
            i += 1

    def apply_force(self):
        for kind, lo, hi in self.user['force']:
            if kind == 'code':
                i = lo
                while i <= hi:
                    d = self.decode(i)
                    if not d:
                        sys.exit('forced code at $%04X is not a valid instruction' % cpu(i))
                    self.set_instr(i, d[2])
                    i += d[2]
                continue
            clash = [j for j in range(lo, hi + 1) if self.cdl[j] & CDL_CODE]
            if clash:
                print('WARNING: forced %s $%04X-$%04X overrides %d byte(s) the CDL saw as code (first $%04X)'
                      % (kind, cpu(lo), cpu(hi), len(clash), cpu(clash[0])))
            for j in range(lo, hi + 1):
                if kind == 'data':
                    self.kind[j] = K_DATA
                    self.locked.add(j)
                else:
                    self.kind[j] = K_WORD
                    if (j - lo) % 2 == 0:
                        self.word_start.add(j)
                        if kind == 'rtswords':
                            self.word_adj[j] = 1
                        t = rom_idx(self.word(j) + self.word_adj.get(j, 0))
                        if t is not None:
                            self.table_targets.add(t)   # traced if unknown (data targets must be marked data)
        for j in range(SIZE - 6, SIZE, 2):              # interrupt vectors
            self.kind[j] = self.kind[j + 1] = K_WORD
            self.word_start.add(j)
            self.table_targets.add(rom_idx(self.word(j)))
        for lo, hi, n, adj, prefix in self.user['splits']:
            for e in range(n):
                a = ((self.rom[hi + e] << 8) | self.rom[lo + e]) + adj
                t = rom_idx(a)
                if t is None:
                    if a >= 0x800:
                        sys.exit('split $%04X/$%04X entry %d is outside RAM and $8000-$FFFF' % (cpu(lo), cpu(hi), e))
                    for j, part in ((lo + e, '<'), (hi + e, '>')):      # pointer to RAM (e.g. a PPU buffer)
                        self.kind[j] = K_DATA
                        self.locked.add(j)
                        self.split_ram[j] = (part, a - adj, adj)
                    continue
                for j, part in ((lo + e, '<'), (hi + e, '>')):
                    self.kind[j] = K_DATA
                    self.locked.add(j)
                    self.split[j] = (part, t, adj)
                self.table_targets.add(t)
        for t, (n, hi, _hdr) in self.user['tables'].items():
            self.row_break.update(range(t, hi + 1, n))

    # ------------------------------------------------------------------ static tracing
    def successors(self, i, mn, mode, n):
        """-> (targets, falls_through, bad)"""
        targets, fall = [], True
        if mn in BRANCHES:
            targets.append(self.rel_dest(i))
        elif mn in ('JSR', 'JMP') and mode == 'abs':
            t = rom_idx(self.operand(i, n))
            if t is None:
                return [], False, True
            targets.append(t)
            fall = mn == 'JSR'
        elif mn in ('JMP', 'RTS', 'RTI'):
            fall = False
        return targets, fall, False

    def speculate(self, i0):
        """decode everything reachable from i0 through unknown bytes; None if it runs into data or garbage"""
        found, cover, stack = {}, set(), [i0]
        while stack:
            i = stack.pop()
            if i in found or self.kind[i] == K_OP:
                continue
            if self.kind[i] != K_UNK or i in cover:
                return None
            d = self.decode(i)
            if not d or d[0] == 'BRK':
                return None
            mn, mode, n = d
            if any(self.kind[i + j] != K_UNK or (i + j) in cover for j in range(n)):
                return None
            found[i] = n
            cover.update(range(i, i + n))
            targets, fall, bad = self.successors(i, mn, mode, n)
            if bad:
                return None
            if fall:
                if i + n >= SIZE:
                    return None
                stack.append(i + n)
            stack.extend(targets)
        return found

    def trace(self):
        changed_any = False
        while True:
            cands = {t for t in self.table_targets if self.kind[t] == K_UNK}
            i = 0
            while i < SIZE:
                if self.kind[i] != K_OP:
                    i += 1
                    continue
                mn, mode, n = self.decode(i)
                targets, fall, bad = self.successors(i, mn, mode, n)
                if not bad:
                    cands.update(t for t in targets if self.kind[t] == K_UNK)
                    if fall and i + n < SIZE and self.kind[i + n] == K_UNK:
                        cands.add(i + n)
                i += n
            changed = False
            for i in sorted(cands):
                if self.kind[i] != K_UNK:
                    continue
                found = self.speculate(i)
                if found:
                    for fi, n in found.items():
                        self.set_instr(fi, n)
                        self.traced.add(fi)
                    changed = changed_any = True
            if not changed:
                return changed_any

    # ------------------------------------------------------------------ pointer tables
    def data_refs(self):
        """ROM addresses read as word tables: the operand of two absolute (indexed) data instructions
        (LDA t,Y / INY / LDA t,Y), or t and t+1 both operands (LDA t,X / LDA t+1,X)"""
        cnt = defaultdict(int)
        for i in self.instrs():
            mn, mode, n = self.decode(i)
            if mode in ('abs', 'abx', 'aby') and mn not in ('JMP', 'JSR', 'STA', 'STX', 'STY'):
                t = rom_idx(self.operand(i, n))
                if t is not None:
                    cnt[t] += 1
        return {t for t, c in cnt.items() if c >= 2 or t + 1 in cnt}

    def free(self, j):
        return self.kind[j] in (K_UNK, K_DATA) and j not in self.locked

    def detect_tables(self):
        """runs of little-endian words that point at code: >= UNREF_MIN words all at known instructions,
        or >= 2 words when the table start is an operand of some LDA tbl,X-like instruction (shorter unreferenced
        tables are declared with 'words' in rom.txt: in 32KB of dense code random byte pairs hit instructions)"""
        refd = self.data_refs()
        found = False
        i = 0
        while i < SIZE - 1:
            best = None
            for adj in (0, 1):
                j, strong, weak = i, 0, 0
                while j + 1 < SIZE and self.free(j) and self.free(j + 1):
                    t = rom_idx(self.word(j) + adj)
                    if t is None:
                        break
                    if self.kind[t] == K_OP:
                        strong += 1
                    elif self.kind[t] == K_UNK and i in refd and self.speculate(t):
                        weak += 1
                    else:
                        break
                    j += 2
                n = strong + weak
                ok = (n >= UNREF_MIN and weak == 0) or (n >= 2 and i in refd and strong >= 1) or (n >= 2 and i in refd and adj == 0)
                if ok and (best is None or n > best[1]):
                    best = (adj, n, strong, weak)
            if not best:
                i += 1
                continue
            adj, n = best[0], best[1]
            for e in range(n):
                j = i + 2 * e
                self.kind[j] = self.kind[j + 1] = K_WORD
                self.word_start.add(j)
                if adj:
                    self.word_adj[j] = 1
                self.table_targets.add(rom_idx(self.word(j) + adj))
            self.table_log.append((i, n, best[2], best[3], adj, i in refd))
            found = True
            i += 2 * n
        return found

    def detect_imm_pairs(self):
        """LDx #lo / STx zp ... LDx #hi / STx zp+1 (either order) where hi:lo is a ROM address"""
        ins = list(self.instrs()) if AUTO_IMM_PAIRS else []
        for k, i in enumerate(ins[:-1]):
            mn, mode, n = self.decode(i)
            if mode != 'imm' or mn not in ('LDA', 'LDX', 'LDY') or i in self.immref:
                continue
            j = ins[k + 1]
            m2, md2, n2 = self.decode(j)
            if m2 != 'ST' + mn[2] or md2 not in ('zp', 'abs') or j != i + n:
                continue
            a1 = self.operand(j, n2)
            if 0x2000 <= a1 < 0x4020:
                continue                                # hardware registers: PPUADDR, SQ1_VOL/SQ1_SWEEP...
            for k2 in range(k + 2, min(k + 10, len(ins) - 1)):
                i2, j2 = ins[k2], ins[k2 + 1]
                m3, md3, n3 = self.decode(i2)
                m4, md4, n4 = self.decode(j2)
                if md3 != 'imm' or m3 not in ('LDA', 'LDX', 'LDY') or m4 != 'ST' + m3[2] or md4 not in ('zp', 'abs'):
                    continue
                a2 = self.operand(j2, n4)
                if abs(a2 - a1) != 1 or 0x2000 <= a2 < 0x4020:
                    continue
                lo_i, hi_i = (i, i2) if a2 == a1 + 1 else (i2, i)
                t = rom_idx((self.rom[hi_i + 1] << 8) | self.rom[lo_i + 1])
                if t is not None and i2 not in self.immref:
                    self.immref[lo_i] = ('<', t)
                    self.immref[hi_i] = ('>', t)
                break
        for lo_i, hi_i in self.user['imms']:
            t = rom_idx((self.rom[hi_i + 1] << 8) | self.rom[lo_i + 1])
            self.immref[lo_i] = ('<', t)
            self.immref[hi_i] = ('>', t)

    # ------------------------------------------------------------------ labels
    def instrs(self):
        i = 0
        while i < SIZE:
            if self.kind[i] == K_OP:
                yield i
                i += self.decode(i)[2]
            else:
                i += 1

    def start_of(self, i):
        while self.kind[i] == K_ARG:
            i -= 1
        if self.kind[i] == K_WORD and i not in self.word_start:
            i -= 1
        if i not in self.user['labels']:
            t = self.table_start(i)
            if t is not None:
                return t
        return i

    def addref(self, t, what):
        self.refs[self.start_of(t)].add(what)

    def note_ram(self, a, mode):
        if a < 0x800:
            self.ram_used[a] += 1
            if mode in ('izx', 'izy', 'ind'):
                self.ptrs.add(a)

    def gather_refs(self):
        for i in self.instrs():
            mn, mode, n = self.decode(i)
            if mode == 'rel':
                self.addref(self.rel_dest(i), 'loc')
            elif mode in ('abs', 'abx', 'aby', 'ind', 'zp', 'zpx', 'zpy', 'izx', 'izy'):
                a = self.operand(i, n)
                t = rom_idx(a)
                if t is not None:
                    self.addref(t, 'sub' if mn == 'JSR' else 'loc' if mn == 'JMP' and mode == 'abs' else 'dat')
                else:
                    self.note_ram(a, mode)
            if i in self.immref:
                self.addref(self.immref[i][1], 'dat')
            if self.cdl[i] & CDL_SUB:
                self.refs[i].add('sub')
            elif self.cdl[i] & CDL_JUMP:
                self.refs[i].add('loc')
        for j in self.word_start:
            self.addref(rom_idx(self.word(j) + self.word_adj.get(j, 0)), 'ptr')
        for j, (_p, t, _a) in self.split.items():
            self.addref(t, 'ptr')
        for j, (_p, a, _a) in self.split_ram.items():
            self.note_ram(a, 'abs')
        for lo, _hi, _n, _adj, _prefix in self.user['splits']:
            self.refs[lo].add('dat')
        for t in self.user['tables']:
            self.refs[t].add('dat')

    def build_labels(self):
        for i, (name, _c) in self.user['labels'].items():
            if self.kind[i] == K_ARG or (self.kind[i] == K_WORD and i not in self.word_start):
                sys.exit('label %s at $%04X is inside an instruction/word' % (name, cpu(i)))
            self.labels[i] = name
        for i, kinds in self.refs.items():
            if i in self.labels:
                continue
            k = self.kind[i]
            if k == K_OP:
                pre = 'sub' if 'sub' in kinds else 'loc'
            elif k == K_WORD:
                pre = 'tbl'
            else:
                pre = 'dat'
            self.labels[i] = '%s_%04X' % (pre, cpu(i))
        self.localize()
        seen = {}
        for i, name in self.labels.items():
            if i in self.local_scope:
                continue
            if name in seen:
                sys.exit('duplicate label %s at $%04X and $%04X' % (name, cpu(seen[name]), cpu(i)))
            seen[name] = i

    def localize(self):
        """Turn branch/JMP-only labels into ca65 cheap locals (@loop, @rts, @skip) when every reference is in the
        same scope (between the same two global labels). rom.txt names starting with '@' must satisfy this too."""
        refs, other = defaultdict(list), set()
        for i in self.instrs():
            mn, mode, n = self.decode(i)
            t = self.rel_dest(i) if mode == 'rel' else rom_idx(self.operand(i, n)) if mn == 'JMP' and mode == 'abs' else None
            if t is None:
                continue
            if self.start_of(t) == t:
                refs[t].append(i)
            else:
                other.add(self.start_of(t))
        user = self.user['labels']
        local = {i for i, name in self.labels.items()
                 if (name.startswith('@') or (i not in user and name.startswith('loc_')))
                 and self.refs.get(i, set()) <= {'loc'} and i not in other and self.kind[i] == K_OP}
        while True:
            breaks = sorted(i for i in self.labels if i not in local)
            scope = lambda p: breaks[bisect.bisect_right(breaks, p) - 1] if breaks and breaks[0] <= p else None
            bad = {t for t in local if any(scope(r) != scope(t) for r in refs[t])}
            if not bad:
                break
            local -= bad
        for i, name in self.labels.items():
            if name.startswith('@') and i not in local:
                sys.exit('local label %s at $%04X is used outside its scope' % (name, cpu(i)))
        by_scope = defaultdict(list)
        for t in sorted(local):
            by_scope[scope(t)].append(t)
        for sc, ts in by_scope.items():
            used = {self.labels[t] for t in ts if t in user}
            for t in ts:
                self.local_scope[t] = sc
                if t in user:
                    continue
                base = '@loop' if any(r > t for r in refs[t]) else '@rts' if self.decode(t)[0] == 'RTS' else '@skip'
                k, name = 1, base
                while name in used:
                    k += 1
                    name = '%s%d' % (base, k)
                used.add(name)
                self.labels[t] = name

    def global_name(self, i):
        """label usable outside ca65 (Mesen .mlb): Scope_local for cheap locals"""
        name = self.labels[i]
        if i in self.local_scope:
            sc = self.local_scope[i]
            return '%s_%s' % (self.labels[sc] if sc is not None else 'Top', name[1:])
        return name

    def target_expr(self, t):
        s = self.start_of(t)
        return self.labels[s] + ('+%d' % (t - s) if t != s else '')

    def ram_name(self, a):
        for b in range(a, max(-1, a - 0x400), -1):
            for tab in (self.ram_user, self.chr_labels):
                if b in tab and a < b + tab[b][1]:
                    return self.member_expr(tab[b][0], a - b)
        if a in self.ptrs:
            return 'ptr_%02X' % a
        if a - 1 in self.ptrs and a not in self.ram_used_direct:
            return 'ptr_%02X+1' % (a - 1)
        return 'ram_%04X' % a

    def member_expr(self, var, off):
        """var+off; for an array of structs: var+element+Struct::field (offset 0 of element 0 stays var)"""
        st = self.consts['arrays'].get(var)
        if st is None or off == 0:
            return var + ('+%d' % off if off else '')
        fields = self.consts['structs'][st]
        size = sum(f[2] for f in fields)
        elem, rest = divmod(off, size)
        fn, foff, _sz = [f for f in fields if f[1] <= rest][-1]
        if foff == 0 and elem:
            return '%s+%d' % (var, off)             # start of an element: no field name
        return '%s%s+%s::%s%s' % (var, '+%d' % (elem * size) if elem else '', st, fn,
                                  '+%d' % (rest - foff) if rest != foff else '')

    def addr_expr(self, i, a):
        if a < 0x800:
            for lo, hi, ram, size, name, _c in self.user['aliases']:
                if lo <= i <= hi and ram <= a < ram + size:
                    return name + ('+%d' % (a - ram) if a > ram else '')
            return self.ram_name(a)
        if a in HW_REGS:
            return HW_REGS[a]
        t = rom_idx(a)
        if t is not None:
            return self.target_expr(t)
        self.raw.append((i, '$%04X' % a))
        return '$%04X' % a

    def instr_text(self, i):
        mn, mode, n = self.decode(i)
        if mode == 'imp':
            return mn
        if mode == 'acc':
            return mn + ' A'
        if mode == 'rel':
            return '%s %s' % (mn, self.target_expr(self.rel_dest(i)))
        v = self.operand(i, n)
        if mode == 'imm':
            if i in self.immconst:
                return '%s #%s' % (mn, self.immconst[i])
            if i in self.immref:
                part, t = self.immref[i]
                e = self.target_expr(t)
                return '%s #%s%s' % (mn, part, e if NAME_RE.match(e) else '(%s)' % e)   # <a+1 = (<a)+1 in ca65
            return '%s #$%02X' % (mn, v)
        if i in self.user['refs']:
            name, off, e = self.user['refs'][i]
            t = next((k for k, (nm, _c) in self.user['labels'].items() if nm == name), None)
            if t is None or cpu(t) + off != v or n != 3:
                sys.exit('ref $%04X: %s is not the operand $%04X' % (cpu(i), e, v))
        else:
            e = self.addr_expr(i, v)
        if n == 3 and v < 0x100 and mode != 'ind':
            e = 'a:' + e                                # keep absolute addressing of a zero page address
        return mn + ' ' + {'zp': '%s', 'abs': '%s', 'zpx': '%s,X', 'abx': '%s,X', 'zpy': '%s,Y', 'aby': '%s,Y',
                           'izx': '(%s,X)', 'izy': '(%s),Y', 'ind': '(%s)'}[mode] % e

    # ------------------------------------------------------------------ output
    def flags(self, i):
        f = self.cdl[i]
        return '%s%s%s%s' % ('C' if f & CDL_CODE else 'T' if i in self.traced else '-', 'D' if f & CDL_DATA else '-',
                             'J' if f & CDL_JUMP else '-', 'S' if f & CDL_SUB else '-')

    def comment(self, i, n, extra=''):
        return ';%s %05X %04X: %s%s' % (self.flags(i), 16 + PAD + i, cpu(i),
                                       ' '.join('%02X' % b for b in self.rom[i:i + n]), extra)

    def stops(self, j, i0):
        return j != i0 and (j in self.labels or j in self.user['notes'] or j in self.row_break
                            or j in self.user['modules'])

    def module_ranges(self):
        """[(name, description, first idx, end idx)] of the PRG source files (one "prg" file without modules)"""
        mods = self.user['modules'] or {0: ('prg', '')}
        if 0 not in mods:
            sys.exit('rom.txt: the first module must start at $%04X' % BASE)
        starts = sorted(mods)
        return [(mods[a][0], mods[a][1], a, b) for a, b in zip(starts, starts[1:] + [SIZE])]

    def emit(self):
        """{module name: source text}"""
        files, out = {}, None
        ranges = {a: (n, d, b) for n, d, a, b in self.module_ranges()}
        i = 0
        while i < SIZE:
            if i in ranges:
                name, desc, end = ranges[i]
                out = ['; Spartan X - %s, CPU $%04X-$%04X%s' % (name, cpu(i), cpu(end - 1),
                                                                          (': ' + desc) if desc else ''),
                       '; Generated by tools/disasm.py (the line comment format is described in src/main.asm).', '']
                files[name] = out
            for lo, _hi, ram, _size, name, c in self.user['aliases']:
                if lo == i:
                    out.append('%-33s ; %s' % ('%s = %s' % (name, self.ram_name(ram)), c) if c
                               else '%s = %s' % (name, self.ram_name(ram)))
            for text in self.user['notes'].get(i, []):
                out.append('; ' + text)
            if i in self.user['tables']:
                n_, hi, hdr = self.user['tables'][i]
                out.append('; table $%04X-$%04X, %d bytes per row%s' % (cpu(i), cpu(hi), n_, (': ' + hdr) if hdr else ''))
            if i in self.labels:
                c = self.user['labels'].get(i, (None, ''))[1]
                out.append('%-33s ; %s' % (self.labels[i] + ':', c) if c else self.labels[i] + ':')
            k = self.kind[i]
            if k == K_OP:
                n = self.decode(i)[2]
                out.append('  %-31s %s' % (self.instr_text(i), self.comment(i, n)))
                i += n
            elif k == K_WORD:
                adj = self.word_adj.get(i, 0)
                t = rom_idx(self.word(i) + adj)
                out.append('  %-31s %s' % ('.word %s%s' % (self.target_expr(t), '-1' if adj else ''),
                                           self.comment(i, 2, '  RTS-trick' if adj else '')))
                i += 2
            elif i in self.split_ram:
                part, a, adj = self.split_ram[i]
                out.append('  %-31s %s' % ('.byte %s(%s%s)' % (part, self.ram_name(a), '-%d' % adj if adj else ''),
                                           self.comment(i, 1)))
                i += 1
            elif i in self.split:
                part, t, adj = self.split[i]
                out.append('  %-31s %s' % ('.byte %s(%s%s)' % (part, self.target_expr(t), '-%d' % adj if adj else ''),
                                           self.comment(i, 1)))
                i += 1
            elif self.text_end(i) is not None:
                hi, j = self.text_end(i), i
                while j <= hi and j - i < 32 and not self.stops(j, i):
                    j += 1
                out.append('  %-31s %s' % ('.byte ' + self.text_expr(self.rom[i:j]), self.comment(i, 0, '-%04X' % cpu(j - 1))))
                i = j
            elif i in self.user['num16']:
                big = self.user['num16'][i] == 'dbyt'
                v = self.rom[i] << 8 | self.rom[i + 1] if big else self.rom[i] | self.rom[i + 1] << 8
                c = self.consts['datacomment'].get(i)
                out.append('  %-31s %s' % ('%s $%04X' % ('.dbyt' if big else '.word', v),
                                           self.comment(i, 2, ('  ' + c) if c else '')))
                i += 2
            elif i in self.dataexpr:
                ts = self.table_start(i)
                width = self.user['tables'][ts][0] if ts is not None else 8
                j = i
                while j < SIZE and j in self.dataexpr and j - i < width and not self.stops(j, i) \
                        and (j == i or j not in self.consts['datacomment']) \
                        and (j == i or sum(len(self.dataexpr[x]) + 2 for x in range(i, j + 1)) <= 72):
                    j += 1
                c = self.consts['datacomment'].get(i)
                out.append('  %-31s %s' % ('.byte ' + ', '.join(self.dataexpr[x] for x in range(i, j)),
                                           self.comment(i, j - i, ('  ' + c) if c else '')))
                i = j
            else:
                j = i
                while j < SIZE and self.rom[j] == self.rom[i] and self.kind[j] == k and j not in self.split and j not in self.split_ram \
                        and j not in self.dataexpr and not self.stops(j, i):
                    j += 1
                if j - i >= FILL_MIN:
                    out.append('  %-31s %s' % ('.res %d, $%02X' % (j - i, self.rom[i]),
                                               self.comment(i, 0, '-%04X%s' % (cpu(j - 1), ' unknown' if k == K_UNK else ''))))
                    i = j
                    continue
                ts = self.table_start(i)
                width = self.user['tables'][ts][0] if ts is not None else 8
                j = i
                while j < SIZE and j - i < min(width, 16) and self.kind[j] == k and j not in self.split and j not in self.split_ram \
                        and j not in self.dataexpr and not self.stops(j, i):
                    if j > i and self.run_len(j, k) >= FILL_MIN:
                        break
                    j += 1
                out.append('  %-31s %s' % ('.byte ' + ', '.join('$%02X' % b for b in self.rom[i:j]),
                                           self.comment(i, j - i, ' unknown' if k == K_UNK else '')))
                i = j
        return {n: '\n'.join(o) + '\n' for n, o in files.items()}

    def table_start(self, i):
        for t, (_n, hi, _h) in self.user['tables'].items():
            if t <= i <= hi:
                return t
        return None

    def text_end(self, i):
        for t, hi in self.user['texts'].items():
            if t <= i <= hi:
                return hi
        return None

    @staticmethod
    def text_expr(data):
        """printable runs as "..." strings, everything else as $XX"""
        parts, s = [], ''
        for b in data:
            if 0x20 <= b < 0x7F and b != 0x22:
                s += chr(b)
                continue
            if s:
                parts.append('"%s"' % s)
                s = ''
            parts.append('$%02X' % b)
        if s:
            parts.append('"%s"' % s)
        return ', '.join(parts)

    def run_len(self, j, k):
        e = j
        while e < SIZE and self.rom[e] == self.rom[j] and self.kind[e] == k and e not in self.split and e not in self.split_ram:
            e += 1
        return e - j

    def ram_inc(self):
        lines = ['; RAM labels. Generated by tools/disasm.py from tools/labels/ram.txt',
                 '; name = $address ; usage count in code ; comment', '']
        rows = {}
        for a, (name, size, c) in self.ram_user.items():
            rows[a] = (name, sum(self.ram_used[a + k] for k in range(size)),
                       ('(size %d) ' % size if size > 1 else '') + c)
        for a, cnt in self.ram_used.items():
            nm = self.ram_name(a)
            if '+' in nm or nm in self.chr_names or nm in (r[0] for r in rows.values()):
                continue
            rows[a] = (nm, cnt + (self.ram_used.get(a + 1, 0) if a in self.ptrs else 0),
                       'auto: indirect pointer (size 2)' if a in self.ptrs else '')
        for a in sorted(rows):
            name, cnt, c = rows[a]
            lines.append('%-22s = $%04X ; %d%s' % (name, a, cnt, (' ; ' + c) if c else ''))
        return '\n'.join(lines) + '\n'

    # ------------------------------------------------------------------ constants
    def apply_consts(self):
        c = self.consts
        names = {n: a for a, (n, _s, _c) in self.ram_user.items()}
        names.update({n: a for a, n in HW_REGS.items()})
        names.update({self.labels[i]: cpu(i) for i in self.labels if not self.labels[i].startswith('@')})
        names.update(chrdata.NAMES)                      # labels defined in src/chr.asm
        vals = {}
        for name, expr, _c, where in c['defs']:
            if name in names or name in vals:
                sys.exit('%s: constant %s clashes with another name' % (where, name))
            vals[name] = eval_expr(expr, {**names, **vals}, where)
        self.const_vals = vals
        for var in c['arrays']:
            if var not in names:
                sys.exit('const.txt: array %s: unknown RAM variable' % var)
        notes = {}
        for n, v in vals.items():
            if n.startswith('NOTE_'):
                notes.setdefault(v, n)
        durs = {v >> 5: n for n, v in vals.items() if n.startswith('DUR_')}
        self.chr_asm, self.chr_labels = chrdata.generate(self.chr, vals, notes, durs)
        self.chr_names = {n for n, _s, _c in self.chr_labels.values()}
        names.update({n: a for a, (n, _s, _c) in self.chr_labels.items()})
        allnames = {**names, **vals}
        auto = {}                                       # (address, value) -> constant name
        for name, var, where in c['auto']:
            base, _, off = var.partition('+')
            if base not in names:
                sys.exit('%s: unknown variable %s' % (where, var))
            key = (names[base] + (int(off, 0) if off else 0), vals[name] & 0xFF)
            if key in auto and auto[key] != name:
                sys.exit('%s: %s and %s have the same value for %s' % (where, auto[key], name, var))
            auto[key] = name
        seq = list(self.instrs())
        prev = {}
        for a, b in zip(seq, seq[1:]):
            if a + self.decode(a)[2] == b:
                prev[b] = a
        mem = ('zp', 'abs', 'zpx', 'abx', 'zpy', 'aby')
        reg = {'LDA': 'A', 'LDX': 'X', 'LDY': 'Y', 'CMP': 'A', 'CPX': 'X', 'CPY': 'Y',
               'AND': 'A', 'ORA': 'A', 'EOR': 'A'}
        for i in seq:
            mn, mode, n = self.decode(i)
            if mode != 'imm' or i in self.immref or mn not in reg:
                continue
            var = None
            if mn.startswith('LD'):                     # LDx #v / STx var
                j = i + n
                if prev.get(j) == i:
                    mj, mdj, nj = self.decode(j)
                    if mj == 'ST' + reg[mn] and mdj in mem:
                        var = self.operand(j, nj)
            else:                                       # LDx var / [branches, CPx #] / CPx #v
                j = i
                while j in prev and j not in self.labels:
                    j = prev[j]
                    mj, mdj, nj = self.decode(j)
                    if mj == 'LD' + reg[mn] and mdj in mem:
                        var = self.operand(j, nj)
                        break
                    if not (mj in BRANCHES or (mn[0] == 'C' and mj == mn and mdj == 'imm')):
                        break
            if var is not None and (var, self.rom[i + 1]) in auto:
                self.immconst[i] = auto[(var, self.rom[i + 1])]
        for i, (expr, where) in c['uses'].items():
            mode = self.decode(i)[1] if self.kind[i] == K_OP else None
            if mode != 'imm':
                sys.exit('%s: $%04X is not an instruction with an immediate operand' % (where, cpu(i)))
            if i in self.immref:
                sys.exit('%s: $%04X already is #<label / #>label' % (where, cpu(i)))
            if eval_expr(expr, allnames, where) != self.rom[i + 1]:
                sys.exit('%s: %s != $%02X at $%04X' % (where, expr, self.rom[i + 1], cpu(i)))
            self.immconst[i] = expr
        for i, (expr, where) in c['data'].items():
            if self.kind[i] not in (K_DATA, K_UNK) or i in self.split or i in self.split_ram:
                sys.exit('%s: $%04X is not a data byte' % (where, cpu(i)))
            if eval_expr(expr, allnames, where) & 0xFF != self.rom[i] or not 0 <= eval_expr(expr, allnames, where) < 256:
                sys.exit('%s: %s != $%02X at $%04X' % (where, expr, self.rom[i], cpu(i)))
            self.dataexpr[i] = expr

    def const_inc(self):
        lines = ['; Constants. Generated by tools/disasm.py from tools/labels/const.txt', '']
        blocks = defaultdict(list)
        for k, kind, arg, comment in self.consts['blocks']:
            blocks[k].append((kind, arg, comment))
        in_enum, nxt = False, 0
        for k, d in enumerate(self.consts['defs'] + [(None, None, None, None)]):
            name, expr, comment, _w = d
            for kind, arg, bc in blocks.get(k, []):
                if kind == 'struct':
                    lines.append('%-39s ; %s' % ('.struct ' + arg, bc) if bc else '.struct ' + arg)
                    for fn, _off, size in self.consts['structs'][arg]:
                        lines.append('  %-20s %s' % (fn, '.byte' if size == 1 else '.res %d' % size))
                    lines.append('.endstruct')
                elif kind == 'enum':
                    lines.append('%-39s ; %s' % ('.enum', bc) if bc else '.enum')
                    in_enum, nxt = True, 0
                else:
                    lines.append('.endenum')
                    in_enum = False
            if name is None:
                break
            first, *more = comment.split('\n')
            if in_enum:
                v = self.const_vals[name]
                text = '  ' + name if v == nxt else '  %s = %s' % (name, expr)
                lines.append('%-39s ; %s%s' % (text, ('%d' % v) if v < 10 else '$%02X' % v,
                                               (': ' + first) if first else ''))
                nxt = v + 1
            else:
                lines.append(('%-22s = %-14s ; %s' % (name, expr, first)) if first else '%-22s = %s' % (name, expr))
            lines.extend('%-39s ; %s' % ('', x) for x in more)
        return '\n'.join(lines) + '\n'

    def run(self):
        self.classify_cdl()
        self.apply_force()
        for _ in range(10):
            a = self.trace()
            b = self.detect_tables()
            if not (a or b):
                break
        for i in self.user['bitskip']:
            if self.rom[i] != 0x2C or self.kind[i] != K_OP:
                sys.exit('bitskip $%04X: not a BIT abs instruction' % cpu(i))
            self.kind[i], self.kind[i + 1], self.kind[i + 2] = K_DATA, K_OP, K_ARG
            self.locked.add(i)
        self.detect_imm_pairs()
        self.gather_refs()
        # pointer high bytes used only as ptr+1 are printed as ptr_XX+1 instead of getting their own name
        self.ram_used_direct = {a for a in self.ram_used if a - 1 not in self.ptrs or a in self.ram_user}
        self.build_labels()
        self.apply_consts()

    # ------------------------------------------------------------------ files
    def write_all(self, out):
        def w(path, text):
            p = os.path.join(out, path)
            os.makedirs(os.path.dirname(p) or out, exist_ok=True)
            with open(p, 'w', encoding='utf-8', newline='\r\n' if path.endswith('.bat') else '\n') as f:
                f.write(text)

        mods = self.emit()
        for f in glob.glob(os.path.join(out, 'src', 'prg', '*.asm')) + [os.path.join(out, 'src', 'prg.asm')]:
            if os.path.exists(f) and (os.path.dirname(f).endswith('src') or
                                      os.path.splitext(os.path.basename(f))[0] not in mods):
                os.remove(f)                # src/prg.asm or a module of an earlier split
        for name, text in mods.items():
            w('src/prg/%s.asm' % name, text)
        w('src/ram.inc', self.ram_inc())
        w('src/const.inc', self.const_inc())
        w('src/chr.asm', self.chr_asm)
        w('src/defines.inc', '; Hardware registers. Generated by tools/disasm.py\n\n' +
          ''.join('%-22s = $%04X\n' % (n, a) for a, n in sorted(HW_REGS.items())))
        w('src/main.asm', '\n'.join([
            '; Spartan X (J) - ca65 disassembly. Build: assemble.bat (or make)',
            '', '.include "defines.inc"', '.include "ram.inc"', '.include "const.inc"', '',
            '.segment "HEADER"', '  .incbin "../header.bin"', '',
            '; PRG ROM, CPU $8000-$FFFF (32KB, NROM-256), split into src/prg/*.asm (module lines in tools/labels/rom.txt).',
            '; Line comments: flags C = code (CDL), T = code found by static tracing, D = data read (CDL),',
            ';   J = jump target, S = subroutine entry; then file offset, CPU address, bytes.',
            '; Data that neither the CDL nor the tracer reached is marked "unknown".',
            '.segment "PRG"'] +
            ['%-31s ; $%04X %s' % ('.include "prg/%s.asm"' % n, cpu(a), d) for n, d, a, _b in self.module_ranges()] +
            ['', '.include "chr.asm"', '']))
        open(os.path.join(out, 'header.bin'), 'wb').write(self.header)
        w('config.cfg', CONFIG)
        w('assemble.bat', ASSEMBLE)
        w('compare.bat', COMPARE)
        w('game.mlb', self.mlb())
        self.write_report(os.path.join(out, 'docs', 'coverage.txt'))

    def mlb(self):
        lines = []
        for i in sorted(self.labels):
            c = self.user['labels'].get(i, (None, ''))[1]
            lines.append('P:%X:%s%s' % (PAD + i, self.global_name(i), (':' + c) if c else ''))
        for a, (name, _s, c) in sorted({**self.chr_labels, **self.ram_user}.items()):
            lines.append('R:%X:%s%s' % (a, name, (':' + c) if c else ''))
        return '\n'.join(lines) + '\n'

    def write_report(self, path):
        cnt = defaultdict(int)
        for k in self.kind:
            cnt[k] += 1
        r = ['Game PRG coverage ($8000-$FFFF, %d bytes)' % SIZE, '',
             'code (CDL)       %5d' % sum(1 for i in range(SIZE) if self.kind[i] in (K_OP, K_ARG) and self.cdl[i] & CDL_CODE),
             'code (traced)    %5d' % sum(1 for i in range(SIZE) if self.kind[i] in (K_OP, K_ARG) and not self.cdl[i] & CDL_CODE),
             'data             %5d' % cnt[K_DATA],
             'word tables      %5d' % cnt[K_WORD],
             'unknown          %5d' % cnt[K_UNK], '']
        r.append('Unknown runs:')
        i = 0
        while i < SIZE:
            if self.kind[i] != K_UNK:
                i += 1
                continue
            j = i
            while j < SIZE and self.kind[j] == K_UNK:
                j += 1
            r.append('  $%04X-$%04X %4d  %s' % (cpu(i), cpu(j - 1), j - i,
                                             ' '.join('%02X' % b for b in self.rom[i:min(j, i + 24)]) + (' ...' if j - i > 24 else '')))
            i = j
        r += ['', 'Traced code (not in CDL):']
        i = 0
        while i < SIZE:
            if i not in self.traced:
                i += 1
                continue
            j = i
            while j < SIZE and (j in self.traced or self.kind[j] == K_ARG):
                j += 1
            r.append('  $%04X-$%04X' % (cpu(i), cpu(j - 1)))
            i = j
        r += ['', 'Detected word tables (start, words, strong, weak, rts-adj, referenced):']
        r += ['  $%04X %3d %3d %3d %d %s' % (cpu(i), n, s, wk, adj, ref) for i, n, s, wk, adj, ref in self.table_log]
        r += ['', 'Immediate pairs #<label/#>label: %d' % (len(self.immref) // 2)]
        r += ['  $%04X %s%s' % (cpu(i), p, self.labels[self.start_of(t)]) for i, (p, t) in sorted(self.immref.items())]
        r += ['', 'Raw operands (outside RAM/registers/ROM):']
        r += ['  $%04X %s' % (cpu(i), t) for i, t in self.raw]
        r += ['', 'CDL anomalies (code flag on bytes that do not decode):']
        r += ['  $%04X' % cpu(i) for i in self.anomalies]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(r) + '\n')
        print('code %d (traced %d), data %d, words %d, unknown %d -> %s'
              % (cnt[K_OP] + cnt[K_ARG], len(self.traced), cnt[K_DATA], cnt[K_WORD], cnt[K_UNK], path))


CONFIG = """# ld65 config: iNES header + 32KB PRG (CPU $8000-$FFFF, NROM-256) + 8KB CHR
MEMORY {
    HDR: start = $0000, size = $0010, type = ro, file = %O, fill = yes, fillval = $FF;
    PRG: start = $8000, size = $8000, type = ro, file = %O, fill = yes, fillval = $FF;
    CHR: start = $0000, size = $2000, type = ro, file = %O, fill = yes, fillval = $FF;
}

SEGMENTS {
    HEADER:  load = HDR, type = ro;
    PRG:     load = PRG, type = ro;
    CHR_ROM: load = CHR, type = ro;
}

FILES {
    %O: format = bin;
}
"""

ASSEMBLE = """@echo off
cd /d "%~dp0"
del game.nes game.map game_labels.txt 2>nul
ca65 -g src/main.asm -o src/main.o
if errorlevel 1 exit /b 1
ld65 -C config.cfg -Ln game_labels.txt -m game.map -o game.nes src/main.o
"""

COMPARE = """@echo off
cd /d "%~dp0"
call "%~dp0assemble.bat"
if errorlevel 1 exit /b 1
python tools/compare.py
"""


def build_check(out, rom):
    o = os.path.join(out, 'src', 'main.o')
    for cmd in (['ca65', '-g', os.path.join(out, 'src', 'main.asm'), '-o', o],
                ['ld65', '-C', os.path.join(out, 'config.cfg'), '-Ln', os.path.join(out, 'game_labels.txt'),
                 '-m', os.path.join(out, 'game.map'), '-o', os.path.join(out, 'game.nes'), o]):
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if p.returncode:
            print(p.stdout + p.stderr)
            return False
    ok, msg = compare_roms(os.path.join(out, 'game.nes'), rom)
    print('build: ' + msg)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rom', default=os.path.join(ROOT, ROM_NAME))
    ap.add_argument('--cdl', default=os.path.join(ROOT, CDL_NAME))
    ap.add_argument('--out', default=ROOT)
    ap.add_argument('--labels', default=os.path.join(HERE, 'labels'))
    ap.add_argument('--build', action='store_true', help='assemble and compare with --rom')
    ap.add_argument('--no-chr', action='store_true')
    a = ap.parse_args()
    d = Disasm(a.rom, a.cdl, os.path.join(a.labels, 'ram.txt'), os.path.join(a.labels, 'rom.txt'))
    d.run()
    d.write_all(a.out)
    if not a.no_chr:
        subprocess.run([sys.executable, os.path.join(HERE, 'chr_tool.py'), 'split', '--rom', a.rom, '--out', a.out],
                       check=True)
    if a.build and not build_check(a.out, a.rom):
        sys.exit(1)


if __name__ == '__main__':
    main()
