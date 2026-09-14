# Building a Web Browser Player for Barotrauma `.barodemo` Files

This document is a complete, self-contained specification for an AI agent (or
developer) to build a **web-based replay player** for `BDMO v7` `.barodemo`
files produced by the ASPER `NewWorldStreamer` server plugin for Barotrauma.

You do NOT need access to Barotrauma or the game's source. Everything needed to
parse and render a demo is here.

---

## 1. What a `.barodemo` file is

A `.barodemo` file is a **binary recording of one game round**, captured on a
Barotrauma server. It contains:

- Static submarine geometry (rooms/hulls, walls, doors) in submarine-local coords.
- World outline (level cave/wall polygons).
- Per-tick snapshots (nominally 5 Hz) of character positions, door open states, room
  flooding, and submarine world positions.
- Discrete events (deaths, damage, shots) with coordinates.
- Spawn/despawn and gameplay events.
- Optional voice (compressed Opus packets, per-speaker).

The goal of the web player: load a `.barodemo`, render a **top-down 2D view** of
the submarine + characters + world on an HTML canvas, with a **timeline**
(play/pause/seek/speed), combat markers, chat, and optional voice playback.

### 1.1 Compression — files are stored as `.barodemo.zip`

After a round ends the server compresses the demo in a **background thread** into
a **ZIP archive** named `round_YYYYMMDD_HHmmss.barodemo.zip`. The archive contains
a **single entry** — the raw `.barodemo` binary described below (same byte layout,
just deflated inside the zip). The uncompressed `.barodemo` is deleted after a
successful compress.

So a web player must handle **both**:
- a plain `.barodemo` file (uncompressed binary), and
- a `.barodemo.zip` file (standard ZIP/DEFLATE; read the first entry, then parse
  its bytes exactly as below).

How to detect which one you have:
- ZIP archives start with the bytes `50 4B 03 04` (ASCII `P`,`K`,`0x03`,`0x04`).
- The raw demo starts with `42 44 4D 4F` (ASCII `B`,`D`,`M`,`O`).

In the browser, unzip with any zip library (e.g. `fflate`, `JSZip`):
```js
async function loadDemoBytes(file){
  const buf = new Uint8Array(await file.arrayBuffer());
  // ZIP? "PK\x03\x04"
  if (buf[0]===0x50 && buf[1]===0x4B && buf[2]===0x03 && buf[3]===0x04){
    const { unzipSync } = await import('fflate');           // npm: fflate
    const files = unzipSync(buf);                            // { name: Uint8Array }
    const name = Object.keys(files)[0];                      // single entry
    return files[name].buffer;                               // ArrayBuffer of .barodemo
  }
  return buf.buffer; // already raw .barodemo
}
// then: parseDemo(await loadDemoBytes(file))
```

---

## 2. Binary format (BDMO v7) — EXACT byte layout

- **Endianness: little-endian** for all multi-byte integers and floats.
- **Strings**: `u16` length prefix (number of UTF-8 bytes), followed by that many
  UTF-8 bytes. (In JS: read u16 len, then `TextDecoder('utf-8').decode(bytes)`.)
- Types: `u8`, `i8`, `u16`, `i16`, `u32`, `i32`, `f32` (IEEE-754), `i64`.

Recommended JS reader: wrap an `ArrayBuffer` in a `DataView`, keep a cursor
offset, and write helpers `u8()`, `i8()`, `u16()`, `i16()`, `u32()`, `i32()`,
`f32()`, `i64()` (use `getBigInt64` then `Number()`), and `str()`.

### 2.1 HEADER (at file start, read once)

| Field       | Type   | Notes                                  |
|-------------|--------|----------------------------------------|
| magic       | 4 × u8 | must equal ASCII `B`,`D`,`M`,`O`        |
| version     | u16    | `7` for current ASPER recordings; player also accepts 4, 5, 6 |
| flags       | u16    | bit0 (0x1) = voice, bit1 (0x2) = lobby |
| tickRate    | u8     | nominally `5` (temporarily throttled to 3) |
| startUnixMs | i64    | recording start, Unix ms UTC           |
| serverName  | string |                                        |
| levelSeed   | string |                                        |
| levelName   | string | biome / level type display name        |
| subName     | string | main submarine name                    |
| reserved    | u32    | = 0, ignore                            |

### 2.2 FRAME STREAM

After the header, read frames until end of file. **Each frame starts with a
`u8` frame type**, then a type-specific body. Loop: read type, dispatch.

Frame type constants:
```
0x01 SNAPSHOT
0x02 ORIGIN
0x03 SPAWN
0x04 DESPAWN
0x06 EVENT
0x08 INVENTORY
0x09 VOICE_WITH_CHANNEL
0x0A HULL_ASSIGN
0x10 GEOMETRY
0x11 WORLD
0xFF END
```

#### FRAME_GEOMETRY (0x10) — appears once, near start
```
u16 hullCount
repeat hullCount:
    u16 subId       // submarine ID this hull belongs to (0 = no sub / world)
    u16 id          // hull ID
    i32 x, i32 y, i32 w, i32 h   // SUBMARINE-LOCAL rect (left, top, width, height)
u16 wallCount
repeat wallCount:
    u16 subId
    i32 x, i32 y, i32 w, i32 h   // sub-local rect
    u8  flags       // bit0=horizontal, bit1=platform
u16 doorCount
repeat doorCount:
    u16 subId
    u16 id          // door's Item ID
    i32 x, i32 y, i32 w, i32 h   // sub-local rect
    u8  flags       // bit0=horizontal
```
Store all geometry. Coordinates are **relative to the submarine's world
position** (see snapshot SubPos). To get absolute world coords at time T:
`absX = subPos(subId, T).x + localX`, same for Y.

#### FRAME_WORLD (0x11) — appears once
```
i32 levelW
i32 levelH
u16 polyCount
repeat polyCount:
    u16 ptCount
    repeat ptCount: i32 x, i32 y   // ABSOLUTE world points (closed polygon)
```
These are static cave/level wall outlines in absolute world coordinates. Draw as
thin outlines. There can be many (thousands); consider simplifying/limiting.

#### FRAME_ORIGIN (0x02)
```
i32 originX, i32 originY
```
A coordinate origin used to compress snapshot entity positions into i16. **It is
"sticky"**: remember the latest origin; it applies to all following SNAPSHOT
entity positions until the next ORIGIN frame. Initialize origin to (0,0).

#### FRAME_SPAWN (0x03)
```
u32 timeMs
u16 id            // character ID
string species    // e.g. "human", "crawler"
string name       // display name
i8  teamId        // 0=none/FFA, 1=team1, 2=team2 (others possible)
```
Build a map `id -> {species, name, teamId}`. Used for labels & colors.

#### FRAME_DESPAWN (0x04)
```
u32 timeMs
u16 id
```
(Can be ignored for rendering; keep spawn metadata for labels.)

#### FRAME_SNAPSHOT (0x01) — the per-tick dynamic state
```
u32 timeMs
u16 entCount
repeat entCount:
    u16 id
    i16 posX        // see decoding below
    i16 posY
    u8  flags       // bit0=dead, bit1=facingLeft, bit2=human, bit3=inWater, bit4=ragdoll
    u8  healthByte  // health: healthPct = healthByte * 200 / 255  (0..200%)
u16 doorCount
repeat doorCount:
    u16 id          // door Item ID (matches geometry door id)
    u8  openByte    // openState = openByte / 255  (0=closed, 1=open)
u16 hullWaterCount
repeat hullWaterCount:
    u16 id          // hull ID (matches geometry hull id)
    u8  waterByte   // waterPct = waterByte / 255  (0..100%)
u16 subPosCount
repeat subPosCount:
    u16 subId
    i32 x, i32 y    // ABSOLUTE world position of this submarine at this time
```

**Entity position decoding** (POS_SCALE = 4):
```
worldX = currentOrigin.x + posX * 4
worldY = currentOrigin.y + posY * 4
```

Store each snapshot as: `{ timeMs, entities[], doors Map<id,open>, water
Map<id,pct>, subPos Map<subId,{x,y}> }`. Keep snapshots in an array (they are in
ascending timeMs order).

#### FRAME_EVENT (0x06)
```
u32 timeMs
u8  kind          // 1=death, 2=kill, 3=itemUse, 4=itemPickup,
                  // 10=damage, 11=shot, 99=generic
u16 subjectId
u16 targetId
i32 x, i32 y      // ABSOLUTE world coords of the event
f32 amount        // e.g. damage value
string text
```

#### FRAME_INVENTORY (0x08)
```
u16 count
repeat count:
    u16 itemId
    u8  ownerKind       // 0 world, 1 character, 2 item, 3 hull floor
    u16 ownerId
    string identifier
    u8  conditionByte
    if ownerKind is 0 or 3: i16 x, i16 y // origin-relative, scale 4
```

#### FRAME_VOICE_WITH_CHANNEL (0x09)
```
u32 timeMs
u16 speakerId     // character ID of the speaker (voice is per-player)
u8  radio          // 1 if recorded from radio channel
u16 len
len bytes         // raw compressed Opus packet (mono, 48000 Hz)
```
Voice is recorded **separately per player** (each frame has a speakerId). The
audio codec is **Opus** at 48 kHz mono. For web playback you need an Opus decoder
in the browser (see §6).

#### FRAME_HULL_ASSIGN (0x0A)
```
u32 timeMs
u16 count
repeat count:
    u16 characterId
    u16 hullId       // zero removes the assignment
```

`0x05 CHAT` and `0x07 VOICE` are legacy frame types. Current ASPER recordings
do not emit them: voice uses `0x09` so the radio flag is preserved, and chat is
not recorded by `NewWorldStreamer`.

#### FRAME_END (0xFF)
```
u32 timeMs        // total duration
```
Stop reading. Set `durationMs = timeMs`.

---

## 3. Coordinate system

- Barotrauma world coordinates: **+X is right, +Y is UP** (mathematical, not
  screen). On an HTML canvas Y grows DOWN, so when projecting world→screen you
  must **invert Y**.
- Rect convention from geometry: `(x, y)` is the **top-left** in world space,
  `w` is width, `h` is height. Because world-Y is up, the rect spans from
  `y` (top) down to `y - h` (bottom). When drawing, compute both corners through
  the world→screen transform and take min/max.

World→screen projection (with a pan camera `cam.x, cam.y` and `cam.zoom`):
```
screenX = canvasWidth/2  + (worldX - cam.x) * zoom
screenY = canvasHeight/2 - (worldY - cam.y) * zoom   // note the minus (Y flip)
```

---

## 4. Player architecture (suggested)

```
DemoFile (parser)         — parses ArrayBuffer -> { header, geometry, world,
                            snapshots[], chat[], events[], voice[], durationMs }
Timeline                  — currentTimeMs, playing, speed; advances by rAF dt
SnapshotLookup            — binary search snapshots by timeMs (<= t)
Renderer (canvas 2D)      — draws world, sub geometry, doors, chars, combat, HUD
Camera                    — pan (drag), zoom (wheel); optional follow-sub
ChatOverlay               — show chat lines with timeMs <= currentTime
VoicePlayer (optional)    — decode+play Opus frames near currentTime
UI                        — file picker, play/pause, speed buttons, seek slider
```

### Update loop (requestAnimationFrame)
1. Compute real `dt` since last frame.
2. If playing: `currentTimeMs += dt * 1000 * speed`; clamp to `durationMs`.
3. `idx = indexAtTime(currentTimeMs)` (binary search: largest snapshot with
   `timeMs <= currentTimeMs`).
4. Render frame for snapshot `idx`.
5. Update voice (if enabled).

---

## 5. Rendering recipe

Draw order (back to front):
1. **Background** fill (dark, e.g. #0A0E14).
2. **World outline** (FRAME_WORLD polys): thin dim lines (#1E2830). Clip offscreen.
3. **Hulls (rooms)**: for each hull, absX = subPos(subId,idx) + local; draw filled
   rect (#18222C), then a **water overlay** from the bottom up using
   `waterPct` from the snapshot's water map (semi-transparent blue), then a
   room outline (#465A6E).
4. **Walls**: filled rects (#8C96A0; platforms slightly darker).
5. **Doors**: for each geometry door, look up `openState` in snapshot door map.
   Draw the "closed portion" filled; color lerps red(closed)→green(open). A door
   that is horizontal slides along X, vertical along Y.
6. **Characters**: for each snapshot entity:
   - color by `teamId` (lookup spawn map): team0 grey, team1 blue, team2 orange;
     non-human reddish; dead = dark grey.
   - draw a square marker; a short line in facing direction (flags bit1 = left).
   - health bar above (from healthPct), name label for humans.
7. **Combat events**: iterate events within a time window of currentTime
   (e.g. `0 <= currentTime - ev.timeMs <= 700ms`), fade by recency:
   - kind 10 (damage): expanding red flash at (x,y), size scaled by `amount`.
   - kind 11 (shot): a line from event (x,y) to the shooter's current position
     (lookup subjectId in current snapshot entities) + a yellow dot.
8. **HUD**: time `mm:ss / mm:ss`, speed, play state. **Chat** lines bottom-left.

### Camera
- Mouse wheel: multiply zoom by ~1.1 / 0.9, clamp (e.g. 0.005..2.0).
- Right-drag (or left-drag): pan by `delta / zoom`.
- Initial framing: compute bounds from all entities + hull rects (offset by the
  first snapshot's subPos) and fit zoom so the scene fills ~80% of the canvas.
- Optional "follow submarine": each frame set cam to main sub's subPos (the sub
  with the most hulls, or just the first), so the boat stays centered as it moves.

---

## 6. Voice playback in the browser (Opus @ 48kHz mono)

The voice frames are raw Opus packets (NOT in an Ogg container — they are bare
Opus frames as produced by the Concentus encoder, one packet per ~20ms window).

Options, easiest first:
1. **libopus compiled to WASM** (e.g. `libopus.js`, `opus-decoder` npm package
   `@evan/opus` or `opus-decoder`). Feed each packet's bytes to the decoder →
   get Float32/Int16 PCM at 48kHz mono.
2. Buffer decoded PCM into the **Web Audio API**: create an `AudioContext`,
   convert PCM to an `AudioBuffer` (sampleRate 48000, 1 channel), schedule via
   an `AudioBufferSourceNode` at the right time on `audioContext.currentTime`.

Synchronization:
- Track `currentTimeMs` of the player. Maintain a pointer into the voice array.
- While `voice[ptr].timeMs <= currentTimeMs`, decode and schedule that packet,
  advance ptr. On seek/backward jump, binary-search ptr to the new time and
  flush scheduled audio.
- Because voice is per-speaker (speakerId), you can:
  - mix all speakers into one output (simplest), OR
  - keep per-speaker gain nodes for muting, OR
  - pan/attenuate by distance of the speaker's marker from camera center
    (positional audio).
- Show a "speaking" indicator above a character marker whose speakerId has a
  voice packet within ~±150ms of currentTime.

If decoding Opus in-browser is too heavy, an alternative is to **pre-transcode**
voice to WAV/MP3 server-side per speaker and reference them by time — but the
self-contained approach is WASM Opus.

---

## 7. Reference JS parser skeleton

```js
class Reader {
  constructor(buf){ this.dv = new DataView(buf); this.o = 0; }
  u8(){ return this.dv.getUint8(this.o++); }
  i8(){ return this.dv.getInt8(this.o++); }
  u16(){ const v=this.dv.getUint16(this.o,true); this.o+=2; return v; }
  i16(){ const v=this.dv.getInt16(this.o,true); this.o+=2; return v; }
  u32(){ const v=this.dv.getUint32(this.o,true); this.o+=4; return v; }
  i32(){ const v=this.dv.getInt32(this.o,true); this.o+=4; return v; }
  f32(){ const v=this.dv.getFloat32(this.o,true); this.o+=4; return v; }
  i64(){ const v=this.dv.getBigInt64(this.o,true); this.o+=8; return Number(v); }
  bytes(n){ const b=new Uint8Array(this.dv.buffer, this.o, n); this.o+=n; return b; }
  str(){ const n=this.u16(); const b=new Uint8Array(this.dv.buffer, this.o, n);
         this.o+=n; return new TextDecoder('utf-8').decode(b); }
}

function parseDemo(arrayBuffer){
  const r = new Reader(arrayBuffer);
  if (r.u8()!==66||r.u8()!==68||r.u8()!==77||r.u8()!==79) throw new Error('bad magic');
  const header = {
    version: r.u16(), flags: r.u16(), tickRate: r.u8(), startUnixMs: r.i64(),
    serverName: r.str(), levelSeed: r.str(), levelName: r.str(), subName: r.str(),
  };
  r.u32(); // reserved
  const hasVoice = (header.flags & 1) !== 0;

  const geometry = { hulls:[], walls:[], doors:[] };
  const world = { w:0, h:0, polys:[] };
  const snapshots = [], chat = [], events = [], voice = [];
  const spawns = new Map();
  let origin = {x:0,y:0}, durationMs = 0;

  const dv = r.dv;
  while (r.o < dv.byteLength) {
    const type = r.u8();
    if (type === 0x10) { // GEOMETRY
      const hc = r.u16();
      for (let i=0;i<hc;i++) geometry.hulls.push({subId:r.u16(), id:r.u16(),
        x:r.i32(), y:r.i32(), w:r.i32(), h:r.i32()});
      const wc = r.u16();
      for (let i=0;i<wc;i++){ const subId=r.u16(), x=r.i32(), y=r.i32(),
        w=r.i32(), h=r.i32(), f=r.u8();
        geometry.walls.push({subId,x,y,w,h, horizontal:!!(f&1), platform:!!(f&2)});}
      const dc = r.u16();
      for (let i=0;i<dc;i++){ const subId=r.u16(), id=r.u16(), x=r.i32(),
        y=r.i32(), w=r.i32(), h=r.i32(), f=r.u8();
        geometry.doors.push({subId,id,x,y,w,h, horizontal:!!(f&1)});}
    } else if (type === 0x11) { // WORLD
      world.w=r.i32(); world.h=r.i32();
      const pc=r.u16();
      for(let i=0;i<pc;i++){ const n=r.u16(); const pts=new Array(n);
        for(let j=0;j<n;j++) pts[j]={x:r.i32(), y:r.i32()}; world.polys.push(pts);}
    } else if (type === 0x02) { origin = {x:r.i32(), y:r.i32()}; }
    else if (type === 0x03) { const t=r.u32(), id=r.u16(); const sp={id,
      species:r.str(), name:r.str(), teamId:r.i8()};
      if(header.version>=7) sp.job=r.str();
      spawns.set(id,sp); }
    else if (type === 0x04) { r.u32(); r.u16(); }
    else if (type === 0x01) { // SNAPSHOT
      const timeMs=r.u32(); const ec=r.u16(); const ents=new Array(ec);
      for(let i=0;i<ec;i++){ const id=r.u16(), px=r.i16(), py=r.i16(),
        fl=r.u8(), hb=r.u8(), oxygen=header.version>=7?r.u8():0;
        ents[i]={id, x:origin.x+px*4, y:origin.y+py*4,
          dead:!!(fl&1), facingLeft:!!(fl&2), human:!!(fl&4),
          inWater:!!(fl&8), ragdoll:!!(fl&16), healthPct: hb*200/255, oxygen};}
      const doors=new Map(); const ddc=r.u16();
      for(let i=0;i<ddc;i++){ doors.set(r.u16(), r.u8()/255); }
      const water=new Map(); const hwc=r.u16();
      for(let i=0;i<hwc;i++){ water.set(r.u16(), r.u8()/255); }
      const subPos=new Map(); const spc=r.u16();
      for(let i=0;i<spc;i++){ const sid=r.u16(); subPos.set(sid,{x:r.i32(),y:r.i32()});}
      snapshots.push({timeMs, ents, doors, water, subPos});
      durationMs = Math.max(durationMs, timeMs);
    } else if (type === 0x06) { // EVENT
      events.push({timeMs:r.u32(), kind:r.u8(), subjectId:r.u16(),
        targetId:r.u16(), x:r.i32(), y:r.i32(), amount:r.f32(), text:r.str()});
      durationMs = Math.max(durationMs, events[events.length-1].timeMs); }
    else if (type === 0x08) { // INVENTORY
      const count=r.u16();
      for(let i=0;i<count;i++){
        r.u16(); const ownerKind=r.u8(); r.u16(); r.str(); r.u8();
        if(ownerKind===0||ownerKind===3){r.i16();r.i16();}
      }
    } else if (type === 0x09) { // VOICE_WITH_CHANNEL
      const timeMs=r.u32(), speakerId=r.u16(), radio=r.u8(), len=r.u16();
      const data=r.bytes(len).slice(); // copy out
      voice.push({timeMs, speakerId, radio, data}); }
    else if (type === 0x0A) { // HULL_ASSIGN
      r.u32(); const count=r.u16(); r.skip(count*4);
    }
    else if (type === 0xFF) { durationMs=Math.max(durationMs, r.u32()); break; }
    else { throw new Error('unknown frame 0x'+type.toString(16)+' at '+(r.o-1)); }
  }
  return {header, hasVoice, geometry, world, snapshots, chat, events, voice,
          spawns, durationMs};
}
```

### Snapshot lookup (binary search)
```js
function indexAtTime(snapshots, t){
  let lo=0, hi=snapshots.length-1, res=0;
  while(lo<=hi){ const m=(lo+hi)>>1;
    if(snapshots[m].timeMs<=t){res=m; lo=m+1;} else hi=m-1; }
  return res;
}
function subPosAt(snap, subId){ return snap.subPos.get(subId) || {x:0,y:0}; }
```

---

## 8. Color & style reference (match the in-game player)

```
background     #0A0E14
worldOutline   #1E2830
hullFill       #18222C
water          rgba(40,90,140,0.66)
hullOutline    #465A6E
wall           #8C96A0  (platform #5A646E)
doorClosed     #C8463C
doorOpen       #50C86E
team0          #B4B4B4   team1 #5AA0FF   team2 #FF8C5A
creature       #C85A5A   dead #5A5A5A
healthbar      lerp red(#DC4646)->green(#5ADC5A)
damageFlash    #FF5028
shotLine       #FFE678
chatText       #DCDCB4
```

---

## 9. Build / packaging suggestions

- Plain static site: `index.html` + one ES module. No backend needed; user picks
  a `.barodemo` via `<input type="file">`, you `file.arrayBuffer()` then
  `parseDemo()`. Render on a `<canvas>`.
- For a server-hosted gallery: list files from a directory, fetch a chosen one
  with `fetch(url).then(r=>r.arrayBuffer())`.
- Voice: lazy-load the Opus WASM decoder only if `hasVoice` and the user enables
  audio (browser autoplay policy requires a user gesture to start AudioContext).

---

## 10. Robustness notes for the implementer

- Validate magic + version; current ASPER recordings use version 7. The deployed
  player supports versions 4 through 7 with version-specific frame layouts.
- Files can be large (tens of MB; more with voice). Parse once into memory; if
  memory is a concern, parse lazily or stream, but the simple approach (parse all)
  is fine up to a few hundred MB.
- World polygons can number in the thousands — consider drawing only those whose
  bounding box intersects the viewport, and/or decimating points.
- Always clip offscreen geometry before drawing for performance.
- Timestamps are milliseconds from recording start (`startUnixMs` is just for
  display/metadata). Use `timeMs` for the timeline.
- Geometry (hulls/walls/doors) is in **submarine-local** coordinates; you MUST
  add the submarine's per-snapshot world position (`subPos[subId]`) to place it,
  otherwise the boat won't move with the characters.
- Events and world polygons are already in **absolute** world coordinates.

---

## 11. Minimal feature checklist for the web player

- [ ] File picker + parse + error display (+ accept `.barodemo.zip`: unzip first entry)
- [ ] Canvas render: world outline, hulls+water, walls, doors, characters
- [ ] Timeline: play/pause, speed (0.5/1/2/4), seek slider, time display
- [ ] Camera: zoom (wheel), pan (drag), initial auto-fit, optional follow-sub
- [ ] Character labels (names), team colors, facing, health bars
- [ ] Combat overlay: damage flashes, shot lines (windowed by time)
- [ ] Chat overlay (cumulative up to current time)
- [ ] (Optional) Voice: Opus WASM decode + Web Audio scheduling, per-speaker
- [ ] (Optional) "speaking" indicator above markers, mute list, positional audio
```
