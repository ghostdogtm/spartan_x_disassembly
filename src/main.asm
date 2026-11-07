; Spartan X (J) - ca65 disassembly. Build: assemble.bat (or make)

.include "defines.inc"
.include "ram.inc"
.include "const.inc"

.segment "HEADER"
  .incbin "../header.bin"

; PRG ROM, CPU $8000-$FFFF (32KB, NROM-256), split into src/prg/*.asm (module lines in tools/labels/rom.txt).
; Line comments: flags C = code (CDL), T = code found by static tracing, D = data read (CDL),
;   J = jump target, S = subroutine entry; then file offset, CPU address, bytes.
; Data that neither the CDL nor the tracer reached is marked "unknown".
.segment "PRG"
.include "prg/tables.asm"       ; $8000 jump/pointer tables and small data tables
.include "prg/main.asm"         ; $8258 reset, NMI, game modes, PPU and joypad helpers
.include "prg/gfx_data.asm"     ; $9010 PPU strings, object tables, metasprites
.include "prg/player.asm"       ; $A47D player input and actions, floor scrolling
.include "prg/spawns.asm"       ; $A931 floor events: boss arrival, enemy groups, floor 2 and floor 4 events
.include "prg/enemies.asm"      ; $ACDA object update loop, enemy and object handlers, walking helpers
.include "prg/draw.asm"         ; $B2B5 object sprites, fragments, effects, floor 4 sprites; distances to the player
.include "prg/obj_tables.asm"   ; $BAA0 wall page plaques, Sylvia; knife, hit box, damage, score and HUD tables
.include "prg/knives.asm"       ; $BDE1 knife thrower and its knives
.include "prg/hits.asm"         ; $C08A hit boxes and hit checks, damage and points; floor timer, HUD, score and bonuses
.include "prg/bosses.asm"       ; $CDDF boss tables and handlers, boomerangs, floor 4 boss tricks
.include "prg/ppu_strings.asm"  ; $DAE0 PPU string record writer (NMI)
.include "prg/samples.asm"      ; $DB3E palettes, DPCM samples $DC00-$F830
.include "prg/sound.asm"        ; $F831 sound engine, music data, vectors

.include "chr.asm"
