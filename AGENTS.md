# Spartan X (J) — disassembly

A byte-exact ca65/ld65 disassembly of **Spartan X (J) [!]** (Famicom, mapper 0 NROM-256, 32 KB PRG at $8000-$FFFF, 8 KB CHR, vertical mirroring).
See `README.md` for the overview and `docs/` for what is known about the game code and data formats.

## Build and check

```
assemble.bat / make          -> game.nes
compare.bat  / make check    -> IDENTICAL
make test                    # check + lint_labels + reloc_test + selftest
```

Without `make`: `python tools/disasm.py --build` (generates and compares), `python tools/lint_labels.py`,
`python tools/reloc_test.py`, `python tools/selftest.py`.

## Rules

- Edit the markup (names, code/data/table/text/words, modules) in `tools/labels/rom.txt` and `ram.txt`, operand and
  data constants in `const.txt`, then run `python tools/disasm.py --build`. **`disasm.py` overwrites `src/`** — after
  hand edits of `src/**/*.asm` run it only with `--out DIR`.
- After every change: the build must stay `IDENTICAL`, `lint_labels.py` 0 problems, `reloc_test.py` 0 pointers that
  did not move, `selftest.py` OK.
- No magic numbers: immediates and table data are named with constants from `const.txt` (`use`, `data`, `@var`).
  Only idioms stay numeric (`#0`/`#1`, `EOR #$FF`, `#$FF` as −1).
- Debugging: `mesen\` (Mesen 2) if present. Run **only one instance at a time, without the GUI** (`--testrunner` +
  Lua) through `tools/mesen/run_profile.py` (it works on a copy of Mesen).
- `(?)` in the label files marks an assumption; verify it and remove the mark.
- Language of docs, comments and label files: English (the README exists in English and Ukrainian: `README.md`, `README.uk.md`; keep them in sync).
