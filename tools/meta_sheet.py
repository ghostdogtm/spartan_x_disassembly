#!/usr/bin/env python3
"""Render every object type's metasprite frames into PNG sheets (docs/metasprites/type_TT.png).

Reads the original ROM and the names from tools/labels/rom.txt. Frame format (DrawMetasprite):
byte 0 = columns W, byte 1 = column entries N, byte 2 = first column; then N entries
[tile count, dY, tiles...], one per column, tiles stacked 8 px downwards. Sprites use pattern table $0000,
palette ObjSprAttr[type] & 3 from PaletteTemplate (floor 1 colours).
"""
import argparse, os, re
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROM_NAME = 'Spartan X (J) [!].nes'
BASE = 0x8000

NES_RGB = [int(c, 16) for c in '''
7C7C7C 0000FC 0000BC 4428BC 940084 A80020 A81000 881400 503000 007800 006800 005800 004058 000000 000000 000000
BCBCBC 0078F8 0058F8 6844FC D800CC E40058 F83800 E45C10 AC7C00 00B800 00A800 00A844 008888 000000 000000 000000
F8F8F8 3CBCFC 6888FC 9878F8 F878F8 F85898 F87858 FCA044 F8B800 B8F818 58D854 58F898 00E8D8 787878 000000 000000
FCFCFC A4E4FC B8B8F8 D8B8F8 F8B8F8 F8A4C0 F0D0B0 FCE0A8 F8D878 D8F878 B8F8B8 B8F8D8 00FCFC F8D8F8 000000 000000
'''.split()]


def labels():
    names = {}
    for line in open(os.path.join(ROOT, 'tools', 'labels', 'rom.txt'), encoding='utf-8'):
        m = re.match(r'^([0-9A-F]{4}) ([A-Za-z_]\w*)', line)
        if m:
            names[int(m.group(1), 16)] = m.group(2)
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=os.path.join(ROOT, 'docs', 'metasprites'))
    ap.add_argument('--scale', type=int, default=3)
    args = ap.parse_args()
    data = open(os.path.join(ROOT, ROM_NAME), 'rb').read()
    prg, chr_ = data[16:16 + 0x8000], data[16 + 0x8000:16 + 0xA000]
    names = labels()
    inv = {v: k for k, v in names.items()}
    rd = lambda a: prg[a - BASE]
    word = lambda a: rd(a) | rd(a + 1) << 8
    pal_rec = inv['PaletteTemplate']
    colors = [rd(pal_rec + 3 + i) for i in range(32)]
    sets_addr, attr_addr = inv['ObjectAnimSets'], inv['ObjSprAttr']
    set_ptrs = [word(sets_addr + 2 * t) for t in range(19)]
    # a set ends at the next label (sets overlap: a higher pose reads on into the next set)
    bounds = sorted(a for a in names if a > sets_addr)

    def tile(n, pal):
        img = Image.new('RGBA', (8, 8), (0, 0, 0, 0))
        for y in range(8):
            lo, hi = chr_[n * 16 + y], chr_[n * 16 + y + 8]
            for x in range(8):
                c = (lo >> (7 - x) & 1) | (hi >> (7 - x) & 1) << 1
                if c:
                    rgb = NES_RGB[colors[16 + pal * 4 + c] & 0x3F]
                    img.putpixel((x, y), (rgb >> 16, rgb >> 8 & 255, rgb & 255, 255))
        return img

    def frame(addr, pal):
        w, n, first = rd(addr), rd(addr + 1), rd(addr + 2)
        cols, p = [], addr + 3
        for i in range(n):
            cnt, dy = rd(p), rd(p + 1)
            dy = dy - 256 if dy >= 0x80 else dy
            cols.append((first + i, dy, [rd(p + 2 + k) for k in range(cnt)]))
            p += 2 + cnt
        top = min([dy for _, dy, t in cols if t] + [0])
        bot = max([dy + 8 * len(t) for _, dy, t in cols] + [8])
        img = Image.new('RGBA', (max(w, 1) * 8, bot - top), (0, 0, 0, 0))
        for c, dy, ts in cols:
            for k, tn in enumerate(ts):
                img.alpha_composite(tile(tn, pal), (c * 8, dy - top + 8 * k))
        return img, top

    os.makedirs(args.out, exist_ok=True)
    s = args.scale
    for t in range(19):
        sp = set_ptrs[t]
        end = next(b for b in bounds if b > sp)
        poses = [word(a) for a in range(sp, end, 2)]
        pal = rd(attr_addr + t) & 3 if t < 18 else 0
        frames = [frame(a, pal) for a in poses]
        cell_w = max(64, max(f.width for f, _ in frames) * s + 8)
        cell_h = max(f.height for f, _ in frames) * s + 30
        sheet = Image.new('RGB', (cell_w * len(frames), cell_h), (40, 40, 48))
        d = ImageDraw.Draw(sheet)
        for i, ((img, top), a) in enumerate(zip(frames, poses)):
            big = img.resize((img.width * s, img.height * s), Image.NEAREST)
            sheet.paste(big, (i * cell_w + 4, 4), big)
            d.text((i * cell_w + 4, cell_h - 24), 'pose %d' % i, fill=(255, 255, 0))
            d.text((i * cell_w + 4, cell_h - 12), names.get(a, '$%04X' % a)[:cell_w // 6], fill=(200, 200, 200))
        fn = os.path.join(args.out, 'type_%02X.png' % t)
        sheet.save(fn)
        print('%s: %s, %d poses' % (os.path.relpath(fn, ROOT), names.get(sp, '$%04X' % sp), len(poses)))


if __name__ == '__main__':
    main()
