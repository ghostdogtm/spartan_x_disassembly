#!/usr/bin/env python3
"""Self-test of tools/disasm.py (needs the ROM + CDL in the project root and ca65/ld65 in PATH):

  python tools/selftest.py

Generates the sources into temporary directories twice - with tools/labels and with empty label files - and checks
that each one assembles into a byte-identical ROM and passes the relocation test (no hard-coded ROM addresses).
src/ in the project is not touched.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    out = (p.stdout + p.stderr).strip()
    return p.returncode, out


def case(name, labels):
    tmp = tempfile.mkdtemp(prefix='game_selftest_')
    try:
        rc, out = run([sys.executable, os.path.join(HERE, 'disasm.py'), '--out', tmp, '--labels', labels, '--build'])
        ok = rc == 0 and 'IDENTICAL' in out
        rc2, out2 = run([sys.executable, os.path.join(HERE, 'reloc_test.py'), '--root', tmp, '--labels', labels])
        ok2 = rc2 == 0
        print('%-14s build %s, reloc %s' % (name, 'OK' if ok else 'FAIL', 'OK' if ok2 else 'FAIL'))
        if not ok:
            print(out)
        if not ok2:
            print(out2)
        return ok and ok2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    empty = tempfile.mkdtemp(prefix='game_nolabels_')
    try:
        for f in ('ram.txt', 'rom.txt'):
            open(os.path.join(empty, f), 'w').close()
        results = [case('with labels', os.path.join(HERE, 'labels')), case('no labels', empty)]
    finally:
        shutil.rmtree(empty, ignore_errors=True)
    print('selftest: %s' % ('OK' if all(results) else 'FAILED'))
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
