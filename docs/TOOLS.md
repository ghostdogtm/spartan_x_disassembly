# Tools

## Changes to the template tools

- `disasm.py` handles any NROM size through `BASE`, `SIZE`, `PAD` and `CHR_SIZE` (here $8000, $8000, 0, $2000).
- Unreferenced word runs need at least `UNREF_MIN` = 6 entries. A referenced run must be read through `abs,X/Y` at two sites, or at `t` and `t+1`.
- Targets of the `words` directive are traced as code.
- `split` entries may point to RAM below $0800. These are written as `.byte <(ram_name)`.
- Automatic `#<`/`#>` immediate pairs are off (`AUTO_IMM_PAIRS`). In this game they were mostly false pairs (timers, tiles, adjacent registers); use `imm` when you need one.
- `#<`/`#>` of an expression is put in parentheses, because ca65 reads `>a+1` as `(>a)+1`.
- New `rom.txt` directive `ref AAAA Name[+-N]`: the absolute operand of the instruction at AAAA is written as a label plus an offset. It is checked against the real operand. It is used for tables whose base address lies inside code (`Sq1PotNoisePeriod-$0B,Y`, `NoiseHitVol-1,Y`, `DmcSeqSampleA-1,Y`, `SongOffsets-1,Y`).
- New `rom.txt` directive `alias AAAA-AAAA RAM[/N] name ; comment`: inside that code range the N bytes at RAM (default 1) are written as `name`, `name+1`, ...; the equate `name = ram_name` is printed before AAAA. The range must start at a global label (so the equate does not break the scope of `@local` labels). It names the scratch bytes `ptr_00`..`ptr_07` per routine (`spawnSide`, `spawnTable`, `enemyFacing` in `UpdateBossAndSpawns`).
- `note AAAA ; text` blocks describe the big routines (`Nmi`, `PlayHandler`, `UpdateBossAndSpawns`, `KnifeThrowerHandler`, `SmallEnemyContact`, `DrawObjectState`, `UpdateMusic`); their branch targets have `@local` names.
- Single bytes of `timersFast` / `timersSlow` have their own names in `ram.txt` (`scrollTimer`, `spawnGapTimer`, `bossWaitTimer`, `objActTimer`, ...); the generator picks the nearest name, so the array bases keep their names.
- New tools: `meta_sheet.py` (metasprite sheets in `docs/metasprites/`), `spawn_map.py` (enemy spawn map `docs/spawn_map.md`).

## Debugging in Mesen

```
python tools/mesen/run_profile.py --out DIR --frames 40000 --runs p1:3:1:30000:900:9000
```

The SPEC is `name:seed:players:cheat-until-frame:pause-frame:stall-frames[:start-floor[:passive]]`. `players` 1 = 1P game A, 2 = 2P game A (the bot presses Select until `gameSelect` = 2), 0 = title and demo only.
- Until the cheat frame, the bot keeps `playerHealth` full, keeps the timer at 1000 or more, and caps `bossHealth` at 8.
- `DIR/<name>.progress` gets one line every 300 frames and is flushed at once, so you can follow it during the run.
- The run stops by itself after `stall` frames without a change of mode or floor.
- If the Lua script fails to load, `run_profile.py` kills Mesen after 60 s. Mesen itself would otherwise wait for its 3000 s timeout.
- The runs take roughly 10 frames per second: the exec callback runs Lua on every instruction.
- `--light` drops that callback and the routine-entry hooks (about 150 frames per second; `.exec`/`.args` stay empty,
  `pc` is 0). `--extra FILE ...` appends Lua probes to the script; they see its locals (`frame`, `seen`, `MT`, `CFG`)
  and can add their own callbacks and screenshots. Note: hooks take CPU addresses (the 2nd address column of a
  source line), not the file offset.

In the runs so far, the bot clears floors 1 and 2 and sees the Sylvia scene. It gets stuck at the floor-3 boss: the boss bar goes to $F9, but the floor does not end.
