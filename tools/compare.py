#!/usr/bin/env python3
"""Compare the assembled ROM (game.nes) with the original dump.

  python tools/compare.py [--built game.nes] [--original "Spartan X (J) [!].nes"]

Compares the header, the 32KB PRG and the 8KB CHR byte for byte (disasm.PAD = 0: no padding to strip). Prints IDENTICAL or DIFFERENT; exit status 1 on a difference.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import disasm as d  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--built', default=os.path.join(ROOT, 'game.nes'))
    ap.add_argument('--original', default=os.path.join(ROOT, d.ROM_NAME))
    a = ap.parse_args()
    ok, msg = d.compare_roms(a.built, a.original)
    print(msg)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
