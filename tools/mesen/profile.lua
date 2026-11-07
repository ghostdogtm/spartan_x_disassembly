-- Spartan X (J): headless RAM/code profiler for Mesen 2 (--testrunner).
--
-- tools/mesen/run_profile.py prepends a global table CFG to a copy of this script:
--   out      absolute path prefix of the output files
--   frames   number of frames to run
--   seed     seed of the random bot
--   players  1 or 2 (2 = Select until gameSelect = 2, i.e. 2P game A; 1 = 1P game A); 0 = never press Start (title and demo only)
--   lives    until this frame keep playerHealth full, the floor timer above 1000 and the boss health at most 8
--            (0 = never); afterwards the bot may die
--   pause    frame of play at which Start pauses the game for 120 frames (0 = never)
--   start    first floor 0-4 of the first real game (written to playerFloor on GM_FLOOR_START; 0 = normal)
--   passive  floor 4, while the boss is on screen: 1 = stand still (lets Boss4TryDouble spawn the double),
--            2/3 walk up to the boss (within $1C px), then attack as below,
--            2 = only crouching punches, 3 = only standing kicks
--   stall    stop the run when gameMode / floor have not changed for this many frames (0 = never)
--   reads    also log RAM reads (slow)
--   shots    take a screenshot for each new gameMode, ppuJob value and floor
--   entries  list of routine entry addresses: log the distinct A/X/Y values on entry
--   light    no per-instruction callback (much faster; .exec stays empty and pc is 0 in the other logs)
--
-- Output:
--   <out>.acc    "R|W pc addr count": RAM accesses by instruction (pc = instruction start)
--   <out>.vals   "addr writes changes min max ndistinct v1 v2 ...": values written to each RAM byte
--   <out>.exec   "pc count modes": executed instructions, modes = bit mask of gameMode values
--   <out>.trace  gameMode / displayOn / floor / lives / demoMode changes and every ppuJob request, with the frame
--   <out>.slots  every 15 frames of play: floor, health, score, object slots 0-6, sprite list lengths, sprListB tiles
--   <out>.progress  one line every 300 frames (flushed at once: follow it while the run goes on)
--   <out>.args   "pc A=n:v,v X=n:v,v Y=n:v,v": distinct register values on entry to each routine (CFG.entries)
--   <out>_mode_MM_D.png, <out>_job_JJ.png, <out>_floorNN.png  screenshots (CFG.shots)

local MT = emu.memType.nesMemory
local RAM_MODE, RAM_DISPLAY, RAM_STEP, RAM_JOB = 0x51, 0x50, 0x4F, 0x52
local RAM_LIVES, RAM_PLAYER, RAM_FLOOR, RAM_HEALTH, RAM_DEMO = 0x5C, 0x55, 0x58, 0x4A6, 0x537
local RAM_TIME, RAM_SCROLL = 0x390, 0x12      -- timer digits 1000s 100s 10s 1s; scrollX (page at $65)
local RAM_BOSS_HEALTH = 0x4A5                 -- ENEMY bar
local RAM_SCORE = 0x530                       -- 7 decimal digits
local RAM_OBJ_STATE, RAM_OBJ_TYPE, RAM_OBJ_X, RAM_OBJ_XHI = 0x80, 0x87, 0x8E, 0x9F
local RAM_OBJ_Y, RAM_OBJ_FACING, RAM_OBJ_HEALTH = 0xB0, 0xC0, 0x4A0
local RAM_SPR_LEN, RAM_SPR_LIST_B = 0x4B, 0x6D0   -- sprLenA/B/C; sprListB
local SNAP_EVERY = 15                         -- frames between two .slots lines
local HEALTH_MAX = 0x30
local GM_TITLE, GM_FLOOR_START, GM_PLAY = 0, 1, 2
local startDone = false

local rng = (CFG.seed or 1) % 2147483647
if rng == 0 then rng = 1 end
local function rand(n)
  rng = (rng * 48271) % 2147483647
  return rng % n
end

-- instruction tracking ---------------------------------------------------------------------------------------------------
local pc, modeBit = 0, 1
local execN, execModes = {}, {}
if not CFG.light then emu.addMemoryCallback(function(a)
  pc = a
  execN[a] = (execN[a] or 0) + 1
  execModes[a] = (execModes[a] or 0) | modeBit
end, emu.callbackType.exec, 0x8000, 0xFFFF, emu.cpuType.nes, MT) end

local frame = 0
local trace = io.open(CFG.out .. ".trace", "w")
local seen = {}                          -- screenshot key -> frame to take it

local rd, wr = {}, {}
local wN, wChg, wMin, wMax, wVals, last = {}, {}, {}, {}, {}, {}
if CFG.reads then
  emu.addMemoryCallback(function(a)
    local k = pc * 2048 + a
    rd[k] = (rd[k] or 0) + 1
  end, emu.callbackType.read, 0x0000, 0x07FF, emu.cpuType.nes, MT)
end
-- sound requests ($FB-$FF, non-zero) and state bytes worth tracing when they change
local TRACE_CHANGES = { [0x0372] = "floorGoesRight", [0xBE] = "difficulty", [0x6C] = "timerMode",
  [0x7B] = "bossPhase", [0x7D] = "boss4Trick", [0x039B] = "boss4Double", [0x048F] = "bossDmcSfx", [0x0379] = "attackKind", [0x04A5] = "bossHealth" }
for i = 0, 6 do TRACE_CHANGES[0x87 + i] = "objType" .. i end
emu.addMemoryCallback(function(a, v)
  if a >= 0xFB and a <= 0xFF and v ~= 0 then
    trace:write(string.format("%d snd%02X=%02X pc=%04X mode=%d floor=%d act=%02X pose=%02X\n", frame, a, v, pc,
      emu.read(RAM_MODE, MT), emu.read(0x5F, MT), emu.read(0x69, MT), emu.read(0x68, MT)))
  elseif TRACE_CHANGES[a] and last[a] ~= v then
    trace:write(string.format("%d %s=%02X pc=%04X mode=%d floor=%d page=%d\n", frame, TRACE_CHANGES[a], v, pc,
      emu.read(RAM_MODE, MT), emu.read(0x5F, MT), emu.read(0x65, MT)))
  end
  local k = pc * 2048 + a
  wr[k] = (wr[k] or 0) + 1
  wN[a] = (wN[a] or 0) + 1
  if last[a] ~= v then wChg[a] = (wChg[a] or 0) + 1 end
  last[a] = v
  if not wMin[a] or v < wMin[a] then wMin[a] = v end
  if not wMax[a] or v > wMax[a] then wMax[a] = v end
  local s = wVals[a]
  if not s then s = {} wVals[a] = s end
  s[v] = true
  if CFG.shots and a == 0x7D and v >= 7 and not seen["b4t_" .. v] then seen["b4t_" .. v] = frame + 2 end
  if a == RAM_JOB and v ~= 0 then        -- PPU job request
    trace:write(string.format("%d job=%02X pc=%04X mode=%d\n", frame, v, pc, emu.read(RAM_MODE, MT)))
    if CFG.shots and not seen["job_" .. v] then seen["job_" .. v] = frame + 3 end
  end
end, emu.callbackType.write, 0x0000, 0x07FF, emu.cpuType.nes, MT)

-- bot ----------------------------------------------------------------------------------------------------------------------
local playFrame = 0
local heldLeft, crouch, jump, fighting = 0, false, false, false
local selectDone = false
local pauseLeft = -1

local function pickInput()
  local b = {}
  local mode = emu.read(RAM_MODE, MT)
  local display = emu.read(RAM_DISPLAY, MT)
  if emu.read(RAM_DEMO, MT) ~= 0 then
    if CFG.players > 0 and frame % 60 < 4 then b.start = true end    -- leave the demo
  elseif mode == GM_PLAY and display ~= 0 then
    playFrame = playFrame + 1
    local p = emu.read(RAM_PLAYER, MT)
    local floor = emu.read(RAM_FLOOR + p, MT)
    -- Thomas stops while he attacks: walk for a while, then fight for a while
    if heldLeft <= 0 then
      fighting = not fighting
      crouch = fighting and rand(3) == 0
      jump = fighting and not crouch and rand(3) == 0
      heldLeft = fighting and (6 + rand(20)) or (30 + rand(60))
    end
    heldLeft = heldLeft - 1
    if fighting then
      if crouch then b.down = true end
      local t = playFrame % 8
      if jump and t == 6 then b.up = true end   -- jump, then attack in the air
      if t == 0 then b.b = true elseif t == 4 then b.a = true end
    elseif floor % 2 == 0 then b.left = true else b.right = true end
    if (CFG.passive or 0) > 0 and floor == 3 and emu.read(RAM_OBJ_STATE + 5, MT) ~= 0 and emu.read(RAM_OBJ_XHI + 5, MT) == 1 then
      b = {}                                    -- 1: no attacks, wait for the double
      local bx = emu.read(RAM_OBJ_X + 5, MT)    -- 2/3: walk up to the boss, then attack
      if CFG.passive > 1 and math.abs(bx - 0x80) > 0x1C then
        if bx < 0x80 then b.left = true else b.right = true end
      else
        if CFG.passive == 2 then b.down = true if playFrame % 16 == 0 then b.a = true end end
        if CFG.passive == 3 and playFrame % 16 == 0 then b.b = true end
      end
    end
    if CFG.pause and CFG.pause > 0 then
      if playFrame == CFG.pause then b = { start = true } pauseLeft = 120 end
      if pauseLeft > 0 then pauseLeft = pauseLeft - 1 if pauseLeft == 0 then b = { start = true } end end
    end
  elseif mode == GM_TITLE and CFG.players > 0 then
    local t = frame % 40
    if CFG.players == 2 and emu.read(0x54, MT) < 2 then     -- Select until gameSelect = 2 (2P game A)
      if t >= 10 and t < 16 then b.select = true end
    elseif t >= 20 and t < 26 then
      b.start = true
    end
  end
  return b
end

emu.addEventCallback(function()
  emu.setInput(pickInput(), 0)
end, emu.eventType.inputPolled)

-- per frame ----------------------------------------------------------------------------------------------------------------
local lastKey = ""
local progress = io.open(CFG.out .. ".progress", "w")
local slots = io.open(CFG.out .. ".slots", "w")
local stallKey, stallFrame = "", 0

-- routine entries (CFG.entries, list of CPU addresses): distinct A / X / Y values on entry
local args = {}
for _, e in ipairs(CFG.entries or {}) do
  local t = { a = {}, x = {}, y = {} }
  args[e] = t
  emu.addMemoryCallback(function()
    local s = emu.getState()
    t.a[s["cpu.a"]] = true
    t.x[s["cpu.x"]] = true
    t.y[s["cpu.y"]] = true
  end, emu.callbackType.exec, e, e, emu.cpuType.nes, MT)
end

local function setStr(set)
  local v = {}
  for k in pairs(set) do v[#v + 1] = k end
  table.sort(v)
  local o = {}
  for i = 1, #v do o[i] = string.format("%02X", v[i]) end
  return #v .. ":" .. table.concat(o, ",")
end

local function finish()
  local f = io.open(CFG.out .. ".acc", "w")
  for k, n in pairs(rd) do f:write(string.format("R %04X %03X %d\n", k // 2048, k % 2048, n)) end
  for k, n in pairs(wr) do f:write(string.format("W %04X %03X %d\n", k // 2048, k % 2048, n)) end
  f:close()
  f = io.open(CFG.out .. ".vals", "w")
  for a = 0, 0x7FF do
    if wN[a] then
      local vals = {}
      for v in pairs(wVals[a]) do vals[#vals + 1] = v end
      table.sort(vals)
      local shown = {}
      for i = 1, math.min(#vals, 24) do shown[i] = string.format("%02X", vals[i]) end
      f:write(string.format("%03X %d %d %02X %02X %d %s\n", a, wN[a], wChg[a] or 0, wMin[a], wMax[a], #vals,
        table.concat(shown, " ")))
    end
  end
  f:close()
  f = io.open(CFG.out .. ".exec", "w")
  for a, n in pairs(execN) do f:write(string.format("%04X %d %X\n", a, n, execModes[a])) end
  f:close()
  f = io.open(CFG.out .. ".args", "w")
  for e, t in pairs(args) do
    if next(t.a) then f:write(string.format("%04X A=%s X=%s Y=%s\n", e, setStr(t.a), setStr(t.x), setStr(t.y))) end
  end
  f:close()
  trace:close()
  slots:close()
  emu.stop(0)
end

emu.addEventCallback(function()
  frame = frame + 1
  local mode, display = emu.read(RAM_MODE, MT), emu.read(RAM_DISPLAY, MT)
  local p = emu.read(RAM_PLAYER, MT)
  local floor = emu.read(RAM_FLOOR + p, MT)
  if CFG.start and CFG.start > 0 and not startDone and mode == GM_FLOOR_START and emu.read(RAM_DEMO, MT) == 0 then
    emu.write(RAM_FLOOR + p, CFG.start, MT)   -- first real game: begin on floor CFG.start
    startDone = true
    floor = CFG.start
  end
  modeBit = 1 << (mode & 15)
  local key = string.format("mode=%d display=%d player=%d floor=%d lives=%d,%d demo=%d", mode, display, p, floor,
    emu.read(RAM_LIVES, MT), emu.read(RAM_LIVES + 1, MT), emu.read(RAM_DEMO, MT))
  if key ~= lastKey then
    trace:write(string.format("%d %s\n", frame, key))
    lastKey = key
  end
  if CFG.shots then
    local mk = string.format("mode_%02d_%d", mode, display)
    if not seen[mk] then seen[mk] = frame + 30 end
    local fk = string.format("floor%02d", floor)
    if mode == GM_PLAY and not seen[fk] then seen[fk] = frame + 60 end
  end
  if CFG.shots and CFG.passive == 1 and emu.read(0x039B, MT) ~= 0 and frame % 30 == 0 then
    seen[string.format("double_%06d", frame)] = frame + 1  -- the double: a screenshot every 30 frames
  end
  for k, at in pairs(seen) do
    if at == frame then
      local name = k:match("^job_") and string.format("%s_job_%02X.png", CFG.out, tonumber(k:sub(5)))
        or string.format("%s_%s.png", CFG.out, k)
      local g = io.open(name, "wb")
      g:write(emu.takeScreenshot())
      g:close()
    end
  end
  if CFG.lives and frame < CFG.lives and mode == GM_PLAY then
    emu.write(RAM_HEALTH, HEALTH_MAX, MT)
    if emu.read(RAM_TIME, MT) < 1 then emu.write(RAM_TIME, 1, MT) end
    if emu.read(RAM_BOSS_HEALTH, MT) > 8 and emu.read(RAM_BOSS_HEALTH, MT) < 0x80 then emu.write(RAM_BOSS_HEALTH, 8, MT) end
  end
  if mode == GM_PLAY and display ~= 0 and frame % SNAP_EVERY == 0 then
    -- object slots: state type x xhi y facing health (slots 0-6), then the sprite list lengths and sprListB tiles
    local s = { string.format("%d f=%d boss=%02X hp=%02X lives=%d score=", frame, floor, emu.read(RAM_BOSS_HEALTH, MT),
      emu.read(RAM_HEALTH, MT), emu.read(RAM_LIVES + p, MT)) }
    for i = 0, 6 do s[#s + 1] = tostring(emu.read(RAM_SCORE + i, MT)) end
    for i = 0, 6 do
      s[#s + 1] = string.format(" %d:%02X,%02X,%02X,%02X,%02X,%02X,%02X", i, emu.read(RAM_OBJ_STATE + i, MT),
        emu.read(RAM_OBJ_TYPE + i, MT), emu.read(RAM_OBJ_X + i, MT), emu.read(RAM_OBJ_XHI + i, MT),
        emu.read(RAM_OBJ_Y + i, MT), emu.read(RAM_OBJ_FACING + i, MT), emu.read(RAM_OBJ_HEALTH + i, MT))
    end
    if CFG.shots then                      -- one screenshot per object type seen on screen (key "type_TT")
      for i = 0, 6 do
        local tk = string.format("type_%02X", emu.read(RAM_OBJ_TYPE + i, MT))
        if emu.read(RAM_OBJ_STATE + i, MT) ~= 0 and emu.read(RAM_OBJ_XHI + i, MT) == 1 and not seen[tk] then
          seen[tk] = frame + 1
        end
      end
    end
    local lenB = emu.read(RAM_SPR_LEN + 1, MT)
    s[#s + 1] = string.format(" len=%d,%d,%d B=", emu.read(RAM_SPR_LEN, MT), lenB, emu.read(RAM_SPR_LEN + 2, MT))
    for i = 0, lenB - 1, 4 do s[#s + 1] = string.format("%02X", emu.read(RAM_SPR_LIST_B + i + 1, MT)) end
    slots:write(table.concat(s) .. "\n")
  end
  if frame % 300 == 0 then
    slots:flush()
    progress:write(string.format("%d mode=%d display=%d floor=%d time=%d%d%d%d page=%d scrollX=%d health=%d lives=%d boss=%d\n", frame,
      mode, display, floor, emu.read(RAM_TIME, MT), emu.read(RAM_TIME + 1, MT), emu.read(RAM_TIME + 2, MT),
      emu.read(RAM_TIME + 3, MT), emu.read(RAM_SCROLL + 0x53, MT), emu.read(RAM_SCROLL, MT), emu.read(RAM_HEALTH, MT), emu.read(RAM_LIVES + p, MT), emu.read(RAM_BOSS_HEALTH, MT)))
    progress:flush()
    trace:flush()
  end
  local sk = mode .. ":" .. floor
  if sk ~= stallKey then stallKey, stallFrame = sk, frame end
  if CFG.stall and CFG.stall > 0 and frame - stallFrame >= CFG.stall then
    trace:write(string.format("%d stalled: no gameMode / floor change for %d frames\n", frame, CFG.stall))
    progress:write(string.format("%d stalled\n", frame))
    progress:close()
    finish()
  elseif frame >= CFG.frames then
    progress:close()
    finish()
  end
end, emu.eventType.endFrame)
