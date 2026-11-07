# Spartan X (J) — notes on the game code and data formats

What is known about the game, collected while naming the disassembly. Names are the ones in `tools/labels/*.txt`.
Statements marked "checked in Mesen" were seen in headless emulator runs; the rest comes from reading the code.

## Program structure

| Module | CPU | Contents |
|---|---|---|
| `tables` | $8000 | game-mode jump tables, PPU job and PPU string pointer tables (lo/hi split) |
| `main` | $8258 | RESET, NMI, game-mode dispatch, PPU, joypad and init helpers |
| `gfx_data` | $9010 | PPU strings, object tables, metasprites |
| `player` | $A47D | player input and actions, floor scrolling |
| `spawns` | $A931 | floor events: boss arrival, enemy groups, floor 2 and floor 4 events |
| `enemies` | $ACDA | object update loop, enemy and object handlers, walking helpers |
| `draw` | $B2B5 | object sprites, fragments, effects, floor 4 sprites; distances to the player |
| `obj_tables` | $BAA0 | wall page plaques, Sylvia; knife, hit box, damage, score and HUD tables |
| `knives` | $BDE1 | knife thrower and its knives |
| `hits` | $C08A | hit boxes and hit checks, damage and points; floor timer, HUD, score and bonuses |
| `bosses` | $CDDF | boss tables and handlers, boomerangs, floor 4 boss tricks |
| `ppu_strings` | $DAE0 | PPU string record writer (NMI) |
| `samples` | $DB3E | palettes, then the DPCM samples at $DC00-$F830 |
| `sound` | $F831 | sound engine and music data |

- **Startup.** RESET initialises the hardware and RAM, then loops forever. `warmBootSig` (`$A5,$5A` at $051E) tells a warm reset from power-on.
- **NMI.** The whole game runs in the NMI. Each frame it:
  1. does OAM DMA;
  2. runs the PPU job `ppuJob`;
  3. sets the scroll;
  4. reads the joypads;
  5. updates the timers (`timersFast` every frame, `timersSlow` every 11th) and the 64-bit `rngState`;
  6. calls `RunGameMode`.
- **RunGameMode.** When `displayOn` is set, it jumps to `GameModeHandlers[gameMode]`. When the display is off, it runs `ModeScripts[gameMode]` one entry per frame, using `modeStep` as the index.

## Game modes (checked in Mesen)

| `gameMode` | Name | Seen in the bot runs |
|---|---|---|
| 0 | `GM_TITLE` | title menu 1/2 PLAYER GAME A/B; the demo starts after about 600 frames |
| 1 | `GM_FLOOR_START` | floor set up and shown, about 140 frames; also after losing a life |
| 2 | `GM_PLAY` | play (the demo too: `demoMode` = $0F) |
| 3 | `GM_FLOOR_CLEAR` | boss beaten: walk-off, time bonus counted into the score |
| 4 | `GM_GAME_OVER` | "1-PLAYER GAME OVER" after the last life |
| 5 | `GM_SYLVIA_SCENE` | after floor 2 (and 4): "SAVE SYLVIA FROM MR.X", "HELP ME THOMAS!" |
| 6 | `GM_ENDING` | after floor 5: CONGRATULATIONS texts. Confirmed from the code (its handler sends those PPU jobs), not reached in a run |

## PPU jobs

`ppuJob` (0-$4E) selects one entry of the 79-entry `PpuJobLo`/`PpuJobHi` table. The NMI writes that entry's PPU string records, then clears `ppuJob`.
- Jobs 0-4 and a few others point to `ppuBuffer` (records built at run time).
- Jobs $25-$4E are the player's body frames. **The player is drawn as background tiles** in a 2-tile column in the screen centre; the screen scrolls under him.
- The rest are palettes, attributes, stairs and texts. They are named `PPUJOB_*` in `const.txt`.

Text tiles: `$00-$09` are digits, `$0A-$23` are A-Z, `$FC`/`$FF` are blanks.

## Joypads

`ReadJoypad` shifts the first bit it reads (A) up to bit 7:
`A=$80 B=$40 SELECT=$20 START=$10 UP=$08 DOWN=$04 LEFT=$02 RIGHT=$01`.

## Demo

The demo sets `demoMode = $0F`. The code then writes `SNDCHN = demoMode ^ $0F`, so all channels are off and the demo is silent.

## Objects and sprites (slots checked in Mesen)

- Object slots 0-6: 0-4 enemies, 5 the boss, 6 the player. Fields: `objState` (0 = free), `objType`
  (index into `ObjectHandlers`; floor 1 boss = 3, player = 9), `objX`/`objXHi`, `objY` ($87 on the ground),
  `objFacing`, `objHealth`, `objFallStep`.
  - `objX` is the screen X; the player stays at $80. `objXHi` = 1 means on screen.
  - `objHealth`+5 and +6 are the ENEMY and PLAYER bars (`bossHealth`, `playerHealth`). $FF = knocked out.
  - `objX`/`objXHi` are 17 entries long: slots 7-10 are the floor 4 flyer points (`flyerPointY`), 11-13 the
    fragments of a burst ball, 14-16 the effects.
- `UpdateObjects` runs every slot through `ObjectHandlers[objType]`. While the player is dead, it runs only
  slot 6. A knocked-out object falls (`ApplyObjectFall`) until `objY` reaches $D8, and then `FreeObject` frees
  its slot. For slot 6 (the player), freeing the slot ends the demo or the life (TIME OVER if the timer ran out).
- Enemies come from `UpdateBossAndSpawns` (groups by scroll position, see "Enemy groups"). `DrawPagePlaque` draws the
  wall plaques with the page number (kanji 1-6) as the floor scrolls. `CheckPlayerHits` / `DamageObject` handle the
  player's attacks.
- Sprites are built into three lists of OAM records (Y, tile, attributes, X), with lengths in `sprLenA/B/C`:
  - `sprListA`: the objects (`DrawObjects`)
  - `sprListB`: small sprites: effects (tiles $DB → $DC → $F0), hit sparks, knives, boomerangs and the
    `DrawObjectState` extras (broken pot, dragon breath, smoke columns)
  - `sprListC`: floor 4 only, up to 8 sprites
- In the NMI, `BuildOam` copies the lists to `oamBuffer` from `oamIndex` = $18. On every other frame it swaps
  their order, so sprites past the 8-per-line limit flicker instead of vanishing.
- Object types by floor (Mesen runs; floors 4 and 5 were started through the profiler's start-floor option):

  | Floor | Enemy types (slots 0-4) | Boss type (slot 5) |
  |---|---|---|
  | 1 | 0, 2 (knife thrower) | 3 |
  | 2 | 0, 1, 2, and $0C-$10 (slots 2-3 only) | 4 |
  | 3 | 0, 1, 2 | 5 |
  | 4 | 0, 1, 2, $0A (flyer, slots 0-4), $10, $11 | 6 |
  | 5 | 0, 1, 2 | 7 |

  The boss type is floor + 3. Types 8 and $0B were never seen.
  Floor-only code: `DrawFloor4Sprites` (into `sprListC`) runs only on floor 4, and `DrawBoss5Part` only on
  floor 5.
- Shots and small sprites, each with its own slots and drawn into `sprListB`:
  - knives: 4 slots, one per enemy slot 0-3; `KnifeThrowerHandler` throws them high (Y $90) or low (Y $A8)
  - the floor-2 boss's boomerangs: 2 slots, `SpawnBoomerang`
  - hit sparks: 3 slots
  - fragments of a type-$0C object: 3 slots
  - effects: 3 slots, tiles $DB → $DC → $F0
- `CheckExtraLife`: one extra life per player at 50,000 points (checked in Mesen).
- `tools/mesen/profile.lua` writes `<run>.slots` every 15 frames of play: floor, health, score, slots 0-6,
  list lengths and `sprListB` tiles. It also saves one screenshot per object type (`<run>_type_TT.png`).
  The 7th field of a run spec starts the first game on another floor, e.g.
  `--runs f4:4:1:7000:0:4000:3` starts on floor 4.

## Data formats

**PPU string records** (`PpuStrings`, `ppuBuffer`; written by `WritePpuStrings`):
`addrHi, addrLo, ctrl, data…`, repeated, ending with `$00`.
- ctrl bit 7: vertical increment (+32)
- ctrl bit 6: repeat one data byte
- ctrl bits 0-5: count

**Metasprite sheets**: `python tools/meta_sheet.py` renders every object type's poses into
`docs/metasprites/type_TT.png` (floor 1 palette, pose number and frame label under each frame).

**Metasprites** (`MetaBoss1_0`, `MetaKnifeThrower_7`…, frame pointers in `AnimSet*`; drawn by `DrawMetasprite`):
- header: columns W, number of column entries N, index of the first column;
- then N entries `[tile count, dY, tiles…]`: the tiles are stacked 8 px apart downwards from objY + dY.
- Columns are 8 px apart; X positions are computed only when `objXHi` = 1 and are mirrored when the attribute
  has bit 6 set. The data of a column that falls off the screen is not skipped (it is still read).
- Attributes: `ObjSprAttr[objType]`, | $40 when facing the other way; the left X is objX − `ObjSprOffsetX[objType]`.
- Grippers (type 0) use their own format: `DrawGripper` takes `GripperPoseTiles[pose]` as an offset into
  `GripperTiles`, columns of 5 tiles (+5 when facing right); pose 9 is drawn by `DrawGripperDown`.

**Sound** (`UpdateSound`, once per NMI). Requests are bits in one byte per channel, cleared after every frame:
`sq1SfxQueue` ($FF), `sq2SfxQueue` ($FE), `dmcSfxQueue` ($FD, DMC samples from `DmcStarts`/`DmcLengths`),
`noiseSfxQueue` ($FC) and `musicQueue` ($FB, `MUSIC_*`). An effect on SQ2 pauses the music's SQ2 part.

| musicQueue | Song | Used for |
|---|---|---|
| `MUSIC_BGM` $01 | 9-18 in turn (`bgmPart`) | floor music (Mesen) |
| `MUSIC_HURRY` $02 | 2 | the floor timer reached 3xx (code only) |
| `MUSIC_FLOOR_CLEAR` $04 | 3 | boss beaten (Mesen) |
| `MUSIC_GAME_START` $08 | 4 | once per game (Mesen) |
| `MUSIC_GAME_OVER` $10 | 5 | the last life is lost, just before `GM_GAME_OVER` (Mesen) |
| `MUSIC_ENDING` $20 | 6 | ending (Mesen) |
| `MUSIC_PAUSE` $40 | 7 | pause jingle; `pausedSong` resumes afterwards (Mesen) |
| `MUSIC_UNPAUSE` $80 | 8 | START pressed again during the pause (Mesen) |

- `SongOffsets` ($FCE9) is indexed by song number from 1; each entry is the offset of a song header from $FCE9.
  Several songs share a header (songs 1, 9-12, 15, 16 use `SongHdrBgmA`).
- Header: offset of the song's note-length set in `NoteLengths` (→ `noteLenOffset`), address of the
  song data (lo, hi), then the read offsets of TRI, SQ1 and NOISE inside the data (0 = channel unused).
  `UpdateMusic` always reads 6 bytes, but only the floor-music headers (`SongHdrBgm*`) have the NOISE byte: the
  others are 5 bytes long and their sixth byte is the next header's first byte, unused because NOISE runs only
  while `musicPlaying` bit 0 (floor music) is set.
  SQ2 always starts at offset 0.
- Data: a byte with bit 7 set selects a note length (`GetNoteLen`: `NoteLengths[noteLenOffset + (b & 7)]`
  frames), any other byte is a note: an index into `NotePeriods` (period hi, lo; 0 = rest). $00 ends the channel.
  SQ2 and TRI use this format; SQ1 and NOISE pack both into one byte (`GetNoteLenPacked`): note = bits 1-5,
  length index = bit 0 (×4), bit 7 (×2), bit 6 (×1); a packed byte with no note is silence. NOISE plays only for
  the floor music; all three BGM headers point it at the same track ($FD6C). SQ1 and NOISE have no end marker:
  the song restarts when SQ2 reaches `SONG_END`. Songs share data (the floor-clear TRI part runs on into the
  game-start song, the game-start TRI part into the game-over song, and the ending TRI part ends on the first
  byte of `NotePeriods`).
- In `const.txt` all song bytes are named: `NOTE_x` (pitch of a square channel; the triangle sounds an octave
  lower, `NOTE_REST` = 2), `NL_n` (length index n), `NLP_n` (packed length bits), `SONG_END`.
- Volume envelopes: `Sq1Envelope`, `Sq2Envelope`.

## Player

- `playerInput` ($6A) holds the current player's buttons, taken from `joypads` in play and from the demo script
  in the demo; the floor intro walk writes it too.
- `playerAction` ($69) bits (`PA_*`): bit 0 facing right, bit 1 crouch, bit 2 jump, bit 3 hit, bit 4 kick
  (button B), bit 5 punch (button A).
  `UpdatePlayerAction` runs `PlayerActionHandlers[playerAction & $0E]` (stand, crouch, jump, hit) when
  `actionLockTimer` runs out; `playerPose` counts the animation steps down.
- The player is not made of sprites: his body is background tiles in the middle of the screen. Every frame is
  a PPU string job, `ppuJob` = `PPUJOB_PLAYER` + 0..$29 (`PpuStrings` = frame 0, `PlayerFrame01`…`PlayerFrame29`).
  `SetPlayerFrameJob` picks it: `PlayerFrameSets[attack]` (none, kick, punch) → list by
  `playerAction & (PA_CROUCH|PA_JUMP|PA_HIT)` → byte `playerPose * 2 + facing right`. The lists overlap
  (`PlayerFramesStand` … `PlayerFramesHit`). `PPUJOB_ERASE_PLAYER` blanks the player's tiles.
- `playerWalk` ($67): 0, 1 right, 2 left. While the player walks, `UpdateFloorScroll` scrolls the floor unless an
  enemy is too close; at the ends of the floor the scroll stops and `playerEdgeX` moves instead.
  `spawnStep` advances once every $18 scroll steps and selects the next entry of the floor's spawn table
  (`UpdateBossAndSpawns`).
- Spawn tables (`SpawnTablesLo/Hi` → `SpawnFloor1`…`SpawnFloor5`, $4A bytes each), one byte per `spawnStep`:
  bit 2 set = nothing; bits 6-7 type (0 gripper, $40 small enemy, $80 random of both); bits 3-5 a side pattern
  in `SpawnSides` (4 enemies per group, side xor the player's facing); bits 0-1 select the `SpawnDelays` set.
  A group has 3 or 4 enemies (`spawnLeft`); `GroupDelays` (by floor and `difficulty`) is the pause after a group.
- Floors 2 and 4 first run their own event handler (`FloorEventHandlers`, `floorEvent`): `Floor2Events` drops the
  objects $0C-$11, `Floor4Events` lets flyers out of 4 points; later both switch to `UpdateBossAndSpawns`.
- Every object has a floor position `objWorldX/Hi` (17 entries like `objX`); `WorldToScreenX` turns it into
  `objX/objXHi`. `CalcPlayerDistances` → `objDistance`, `CalcSideOfPlayer` → `objLeftOfPlayer`.
  Positions (`const.txt`): `GROUND_Y` $87 (objY on the floor), `HIDDEN_Y` $A8, floor ends `FLOOR_X_MIN` $0070 and
  `FLOOR_X_MAX_HI:FLOOR_X_MAX` $0690, `HEALTH_FULL` $30.
- `objState` bits (`OBJST_*`): $01 active, $02 spawn facing, $04 a gripper holds the player (handler skipped),
  $08 hidden, $10 alternative behaviour picked at spawn by `EnemyAltChance` (checked in Mesen).

## Enemy groups

`UpdateBossAndSpawns` spawns grippers, small enemies and knife throwers in groups of 4 (3 on floors 1-2 in the
first round) from the floor's spawn table (`SpawnFloor1`..`5`, one byte per `spawnStep` = 24 scroll pixels):
side pattern, delay set and enemy types. Every Nth enemy from one side becomes a knife thrower
(`ThrowerSpawnDelay`); game A waits longer between groups (+$30 frames) and throwers (+2 enemies). The full
per-floor map is in [docs/spawn_map.md](docs/spawn_map.md) (`python tools/spawn_map.py`; from code reading,
not checked in Mesen).

## Floor 2 objects (checked in Mesen)

`Floor2Events` drops objects from the ceiling (Y $38) near the player, `Floor2ObjTypes` first, then random:
`OBJ_BURST` ($0C) bursts into 3 fragments, `OBJ_DRAGON_BALL` ($0D) lands and becomes `OBJ_DRAGON` ($0E, a big
white creature with health), `OBJ_POT` ($0F) breaks (`SFX1_POT_BREAK`) and becomes `OBJ_SNAKE` ($10). At most two
dragon balls/dragons at once. Boss-4 missiles that land may also turn into a dragon or a snake.

## Bosses

Slot 5 holds the boss (`objType` = floor + `OBJ_BOSS1`). Shared state:

- `bossPhase` ($7B): 0 = walk to the player (`BossApproach`, until `BossAttackRange` for the floor), 1 = back off
  (`BossBackOff`, `objSlowTimer+5` from `BossBackOffTime`), 2-4 = attack kind (`ChooseBossAttack`, thresholds
  `BossChoice*` by floor and difficulty). The floor 5 boss also has 5 = jump over a low attack, 6 = block,
  7 = knocked back (`Boss5Phases`).
  Constants: `BOSS_APPROACH`, `BOSS_BACK_OFF`, `BOSS_ATTACK`, `BOSS5_JUMP`, `BOSS5_BLOCK`, `BOSS5_KNOCKED`.
- `bossAttacksLeft` ($7C): attacks before backing off, 1-3 (`SetBossAttackCount`, `BossCount*`; always 3 on floor 2).
- `bossStep` ($7A): step of the current attack; step times come from `Boss1AttackTime` / `BossAttackTime`.
- `objWasHit+5`: the player's hit landed; floors 1 and 3 answer with `BossCounterAttack`.
- Floor 2: boomerangs (`SpawnBoomerang`, `UpdateBoomerangs`, noise effect `SFXN_THROW`).
- Floor 4 (checked in Mesen): `Boss4Cast` throws `OBJ_BOSS4_MISSILE` ($11). A hit with any attack except
  `ATTACK_CROUCH_PUNCH` (`attackKind` 2, from the code at $C279) does no damage and starts `boss4Trick` ($7D):
  1-2 a sprite falls off, 3 the boss vanishes, 4 reappears, 5, then 0. Two hits within 16 frames (`bossHitAgain`)
  also make it vanish. Not confirmed in Mesen yet: in the `passive` 2/3 bot runs the boss kept its distance and its
  missiles kept the player in `PA_HIT`, so no attack landed.
- Floor 4 double (`OBJ_BOSS4_DOUBLE`, checked in Mesen with `run_profile.py` passive mode): `Boss4TryDouble` spawns it
  when `bossWaitTimer` is 0 (set to $9C, about 1716 frames, when the boss arrives; $30 after each double),
  the boss is not casting (`boss4CastTimer`), `boss4Trick` is 0, `boss4InRange` is set (boss on screen within $40 of
  the player) and the player does not punch or kick. It appears on the other side of the player at the boss's
  distance (`boss4Trick` 7, then 0), looks like the boss; after `sceneTimer` = $20 (about 350
  frames) `B4T_SWAP` starts its end: the pair swaps places when needed so the double is the left one, which
  vanishes (8-9), and its slot is freed. Seen at frames 4501, 5451, 6348 of one run (boss on screen from ~2800).
- Floor 5: `Boss5Attack` may block or jump over the player's attack (`Boss5GuardChance`, lower in game B).

## DPCM samples

$DC00-$F830 holds only DMC samples (`DmcStarts`: address = $C000 + 64·n, `DmcLengths`: 16·n + 1 bytes):

| dmcSfxQueue | Sample | Address | Used for (all checked in Mesen) |
|---|---|---|---|
| `DMC_JUMP_KICK` $01 | 1 | $DC00 | jump kick shout |
| `DMC_KICK` $02 | 2 | $DF00 | standing or crouching kick shout |
| `DMC_PLAYER_HIT` $04 | 3 | $E2C0 | the player is hit |
| `DMC_DEFEAT` $08 | 4 | $EC40 | the player is killed, or the boss is beaten |
| `DMC_PUNCH` $20 | 10 | $E380 | punch shout; index 10 reads past both tables ($FFEC, $FFF6) |
| `DMC_SEQ_A` $10 | 5-9 | $E500-$EC3F | 7-step sequence at changing DMC rates (`DmcSeq*`): Sylvia scene (mode 5) |
| `DMC_SEQ_C` $40, `DMC_SEQ_D` $80 | 5-9 | | other sequences |

`bossDmcSfx` ($048F) takes `DMC_SEQ_A/A/C/D/A` by floor (`BossHitDmc`) and is queued by `PlayHandler` $0A slow ticks after the player is knocked out by that hit.

Sample 0 ($EE80) lies inside sample 4 and is never requested.
- Grippers: `grabbers` (+0, +1 per side) count the grippers holding the player; while held, the energy drains
  (`scrollTimer`). `ShakeOffGrabbers`: 4 changes of direction free 1-4 grippers, fewer the longer the player
  has been held (`grabTimer`).

## Patching (examples)

The sources are relocatable ca65, so a hack is an edit of `src/` plus `assemble.bat` (`make`). After hand edits
of `src/` do **not** run `tools/disasm.py` without `--out DIR`: it rewrites `src/`. A patched build is no longer
`IDENTICAL`; compare it with the original to see that only the intended bytes changed. The 32 KB PRG has no free
space at the end (DPCM samples and the vectors fill it), so keep patches the same size or find room first.
Each example below was built on a copy of `src/` and diffed against the dump (Mesen run not done):

| What | Where | Edit | Bytes that change |
|---|---|---|---|
| Starting lives | `START_LIVES` in `src/const.inc` (`LDX #START_LIVES` in `main`) | `= 3` -> `= 5` | $8485 |
| Floor timer speed | `FLOOR_TIME_UNIT` in `src/const.inc` (`TickFloorTimer`: `timerFrac += FLOOR_TIME_STEP`, one unit off per `FLOOR_TIME_UNIT`) | `$34` -> `$68` = the timer runs half as fast | $C70D, $C712 |
| Damage taken | `EnemyDamage` in `src/prg/obj_tables.asm`: by `objType`, game B rows first, game A from +18 | halve every byte | $BC2A-$BC4D |
| Damage dealt | `HitDamage` (+ `HitDamageRow`) in `obj_tables.asm`: by object row + `attackKind` | raise the bytes for a stronger player | $BC60- |
| Knife throwers | `ThrowerSpawnDelay` in `src/prg/tables.asm`: groups between knife throwers by floor*4+difficulty | larger = fewer throwers | $812A-$813D |
| A melody | song data in `src/prg/sound.asm` (`SongBgmA`, ..., format above): `NOTE_*`, `NL_*` | `NOTE_AS2` -> `NOTE_C3` in the last row of `SongBgmA` | $FD40 |

The starting floor time (2000) is not a constant: `SetupFloor` stores the same value 2 (`LDA #1`, `ASL A`) into
`bossActive` and `floorTime` (the thousands digit), so a different start needs a code change of one more byte.
`HURRY_HUNDREDS` sets when the hurry music starts.
