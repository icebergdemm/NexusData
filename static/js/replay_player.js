/* Barotrauma .barodemo web replay player (memory-optimized).
 * Key optimizations vs naive approach:
 * 1. Voice data is NOT stored in memory — skipped during parse, decoded on-demand
 * 2. World polygons stored as flat Int32Array (compact, no per-point objects)
 * 3. Snapshot entities stored as compact typed arrays
 * 4. Original ArrayBuffer freed after parse via explicit nullification
 * 5. Download uses streaming fetch for progress indication
 */
(function () {
  "use strict";

  const C = {
    bg: "#0A0E14", worldOutline: "#1E2830", hullFill: "#18222C",
    water: "rgba(40,90,140,0.66)", hullOutline: "#465A6E",
    wall: "#8C96A0", platform: "#5A646E", doorClosed: "#C8463C", doorOpen: "#50C86E",
    team0: "#B4B4B4", team1: "#5AA0FF", team2: "#FF8C5A",
    creature: "#C85A5A", dead: "#5A5A5A",
    damageFlash: "#FF5028", shotLine: "#FFE678", chatText: "#DCDCB4",
  };
  const POS_SCALE = 4;
  const MAX_SNAPSHOTS = 30000; // ~50 min at 10Hz
  // Sanity limits — guard against misaligned reads producing absurd counts
  const LIM = {
    ent: 4096, door: 4096, water: 8192, subpos: 256,
    hull: 65535, wall: 65535, doorGeo: 65535, poly: 200000, pt: 100000,
    strLen: 8192,
  };

  class Reader {
    constructor(buf) { this.dv = new DataView(buf); this.o = 0; this.len = buf.byteLength; }
    u8() { return this.dv.getUint8(this.o++); }
    i8() { return this.dv.getInt8(this.o++); }
    u16() { const v = this.dv.getUint16(this.o, true); this.o += 2; return v; }
    i16() { const v = this.dv.getInt16(this.o, true); this.o += 2; return v; }
    u32() { const v = this.dv.getUint32(this.o, true); this.o += 4; return v; }
    i32() { const v = this.dv.getInt32(this.o, true); this.o += 4; return v; }
    f32() { const v = this.dv.getFloat32(this.o, true); this.o += 4; return v; }
    i64() { const v = this.dv.getBigInt64(this.o, true); this.o += 8; return Number(v); }
    skip(n) { this.o += n; }
    str() {
      const n = this.u16();
      if (n > 8192 || this.o + n > this.len) throw new Error("Строка вне границ (len=" + n + ") — файл повреждён или несовместим");
      const b = new Uint8Array(this.dv.buffer, this.o, n); this.o += n;
      return new TextDecoder("utf-8").decode(b);
    }
    need(n) { if (this.o + n > this.len) throw new Error("Чтение за границей файла"); }
  }

  function parseDemo(arrayBuffer) {
    const r = new Reader(arrayBuffer);
    if (r.u8()!==66||r.u8()!==68||r.u8()!==77||r.u8()!==79)
      throw new Error("Неверная сигнатура файла (не .barodemo)");
    const header = {
      version: r.u16(), flags: r.u16(), tickRate: r.u8(), startUnixMs: r.i64(),
      serverName: r.str(), levelSeed: r.str(), levelName: r.str(), subName: r.str(),
    };
    r.u32();
    const hasVoice = (header.flags & 1) !== 0;
    if (header.version !== 4) {
      throw new Error("Версия формата " + header.version + " не поддерживается (ожидается 4). Файл может быть повреждён.");
    }
    const geometry = { hulls: [], walls: [], doors: [] };
    const world = { w: 0, h: 0, polys: [] };
    const chat = [], events = [];
    const spawns = new Map();
    let origin = { x: 0, y: 0 }, durationMs = 0;
    // Lazy snapshot index: parallel arrays (time, byte-offset, origin at that point)
    const snapTimes = [], snapOffsets = [], snapOx = [], snapOy = [];
    // Voice: collect compact offset records (no per-packet Uint8Array allocations)
    const voiceT = [], voiceSid = [], voiceOff = [], voiceLen = [];

    while (r.o < r.len) {
      const type = r.u8();
      if (type === 0x10) { // GEOMETRY
        const hc = r.u16(); if(hc>LIM.hull)throw new Error("Битый счётчик hulls="+hc);
        for (let i=0;i<hc;i++) geometry.hulls.push({subId:r.u16(),id:r.u16(),x:r.i32(),y:r.i32(),w:r.i32(),h:r.i32()});
        const wc = r.u16(); if(wc>LIM.wall)throw new Error("Битый счётчик walls="+wc);
        for (let i=0;i<wc;i++){const subId=r.u16(),x=r.i32(),y=r.i32(),w=r.i32(),h=r.i32(),f=r.u8();geometry.walls.push({subId,x,y,w,h,horizontal:!!(f&1),platform:!!(f&2)});}
        const dc = r.u16(); if(dc>LIM.doorGeo)throw new Error("Битый счётчик doors="+dc);
        for (let i=0;i<dc;i++){const subId=r.u16(),id=r.u16(),x=r.i32(),y=r.i32(),w=r.i32(),h=r.i32(),f=r.u8();geometry.doors.push({subId,id,x,y,w,h,horizontal:!!(f&1)});}
      } else if (type === 0x11) { // WORLD — compact flat storage (Int32Array per poly)
        world.w=r.i32();world.h=r.i32();const pc=r.u16();if(pc>LIM.poly)throw new Error("Битый счётчик polys="+pc);
        for(let i=0;i<pc;i++){const n=r.u16();if(n>LIM.pt)throw new Error("Битый счётчик points="+n);
          const flat=new Int32Array(n*2);
          for(let j=0;j<n;j++){flat[j*2]=r.i32();flat[j*2+1]=r.i32();}
          world.polys.push(flat);
        }
      } else if (type === 0x02) { origin={x:r.i32(),y:r.i32()}; }
      else if (type === 0x03) { const t=r.u32(),id=r.u16();spawns.set(id,{id,species:r.str(),name:r.str(),teamId:r.i8()}); }
      else if (type === 0x04) { r.skip(6); } // DESPAWN: u32+u16
      else if (type === 0x01) { // SNAPSHOT — INDEX ONLY (lazy decode at render)
        const snapStart = r.o - 1; // include the frame-type byte
        const timeMs=r.u32(); const ec=r.u16();
        if(ec>LIM.ent)throw new Error("Битый счётчик entities="+ec+" — рассинхрон парсера");
        r.skip(ec*8); // entity = u16 id + i16 x + i16 y + u8 flags + u8 health = 8 bytes
        const ddc=r.u16();if(ddc>LIM.door)throw new Error("Битый счётчик doors="+ddc);
        r.skip(ddc*3); // u16 id + u8 open
        const hwc=r.u16();if(hwc>LIM.water)throw new Error("Битый счётчик water="+hwc);
        r.skip(hwc*3); // u16 id + u8 water
        const spc=r.u16();if(spc>LIM.subpos)throw new Error("Битый счётчик subPos="+spc);
        r.skip(spc*10); // u16 subId + i32 x + i32 y
        snapTimes.push(timeMs); snapOffsets.push(snapStart); snapOx.push(origin.x); snapOy.push(origin.y);
        if(timeMs>durationMs)durationMs=timeMs;
      } else if (type === 0x05) { // CHAT
        chat.push({timeMs:r.u32(),senderId:r.u16(),chatType:r.u8(),author:r.str(),text:r.str()});
      } else if (type === 0x06) { // EVENT
        events.push({timeMs:r.u32(),kind:r.u8(),subjectId:r.u16(),targetId:r.u16(),x:r.i32(),y:r.i32(),amount:r.f32(),text:r.str()});
        const e=events[events.length-1];if(e.timeMs>durationMs)durationMs=e.timeMs;
      } else if (type === 0x07) { // VOICE — record offset, keep bytes in shared buffer
        const t=r.u32(); const sid=r.u16(); const len=r.u16();
        voiceT.push(t); voiceSid.push(sid); voiceOff.push(r.o); voiceLen.push(len);
        r.skip(len); // do not copy now — slice lazily during playback
      } else if (type === 0xFF) { const t=r.u32();if(t>durationMs)durationMs=t;break; }
      else { throw new Error("Неизвестный тип фрейма 0x"+type.toString(16)+" — формат несовместим или файл повреждён"); }
    }
    // Build compact voice index. We keep ONE reference to the source buffer;
    // packets are sliced on demand (small, short-lived) during playback.
    const voice = {
      count: voiceT.length,
      t: Int32Array.from(voiceT),
      sid: Uint16Array.from(voiceSid),
      off: Int32Array.from(voiceOff),
      len: Uint16Array.from(voiceLen),
      buffer: arrayBuffer,
    };
    const snaps = {
      count: snapTimes.length,
      t: Int32Array.from(snapTimes),
      off: (snapOffsets.length && snapOffsets[snapOffsets.length-1] < 2**31) ? Int32Array.from(snapOffsets) : Float64Array.from(snapOffsets),
      ox: Int32Array.from(snapOx),
      oy: Int32Array.from(snapOy),
      buffer: arrayBuffer,
    };
    return {header,hasVoice,geometry,world,snaps,chat,events,spawns,durationMs,voice};
  }

  function indexAtTime(snaps, t) {
    let lo=0,hi=snaps.count-1,res=0;
    while(lo<=hi){const m=(lo+hi)>>1;if(snaps.t[m]<=t){res=m;lo=m+1;}else hi=m-1;}
    return res;
  }

  // Decode a single snapshot on demand from the shared buffer.
  // Returns a transient object (only one is alive at a time during render).
  function decodeSnapshot(snaps, idx) {
    if (idx < 0 || idx >= snaps.count) return null;
    const dv = new DataView(snaps.buffer);
    let o = snaps.off[idx];
    const ox = snaps.ox[idx], oy = snaps.oy[idx];
    o += 1; // skip frame-type byte (0x01)
    const timeMs = dv.getUint32(o, true); o += 4;
    const ec = dv.getUint16(o, true); o += 2;
    const ents = new Array(ec);
    for (let i=0;i<ec;i++){
      const id=dv.getUint16(o,true);o+=2;
      const px=dv.getInt16(o,true);o+=2;
      const py=dv.getInt16(o,true);o+=2;
      const fl=dv.getUint8(o);o+=1;
      const hb=dv.getUint8(o);o+=1;
      ents[i]={id,x:ox+px*POS_SCALE,y:oy+py*POS_SCALE,flags:fl,healthPct:hb*200/255};
    }
    const doors=new Map();const ddc=dv.getUint16(o,true);o+=2;
    for(let i=0;i<ddc;i++){const id=dv.getUint16(o,true);o+=2;doors.set(id,dv.getUint8(o)/255);o+=1;}
    const water=new Map();const hwc=dv.getUint16(o,true);o+=2;
    for(let i=0;i<hwc;i++){const id=dv.getUint16(o,true);o+=2;water.set(id,dv.getUint8(o)/255);o+=1;}
    const subPos=new Map();const spc=dv.getUint16(o,true);o+=2;
    for(let i=0;i<spc;i++){const sid=dv.getUint16(o,true);o+=2;const x=dv.getInt32(o,true);o+=4;const y=dv.getInt32(o,true);o+=4;subPos.set(sid,{x,y});}
    return {timeMs,ents,doors,water,subPos};
  }

  /** Ошибка "не хватило памяти на буфер" (RangeError из new ArrayBuffer/
   * Uint8Array, либо DOMException QuotaExceededError) — отдельный тип,
   * чтобы boot() мог показать понятное сообщение вместо сырого текста
   * движка JS, который ничего не говорит человеку без него. */
  class OutOfMemoryError extends Error {
    constructor(stage) { super(`Не хватило памяти браузера на ${stage}`); this.stage = stage; }
  }
  function isAllocationFailure(e) {
    return e instanceof RangeError || e?.name === "QuotaExceededError" ||
      /allocation failed|array buffer/i.test(e?.message || "");
  }

  async function loadDemoBytes(url, onProgress) {
    onProgress && onProgress({ stage: "download", percent: 0 });
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    // Streaming download with progress
    const total = parseInt(resp.headers.get("content-length") || "0");
    const reader = resp.body.getReader();
    const chunks = []; let received = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value); received += value.length;
      if (onProgress) onProgress({ stage: "download", percent: total ? Math.round(received/total*100) : null, bytes: received, total });
    }
    let buf;
    try {
      buf = new Uint8Array(received);
    } catch (e) {
      if (isAllocationFailure(e)) throw new OutOfMemoryError("скачанный файл (" + fmtBytes(received) + ")");
      throw e;
    }
    let off = 0;
    for (const c of chunks) { buf.set(c, off); off += c.length; }
    chunks.length = 0; // free chunk refs
    // ZIP?
    if (buf[0]===0x50 && buf[1]===0x4B && buf[2]===0x03 && buf[3]===0x04) {
      onProgress && onProgress({ stage: "unpack", percent: null });
      try {
        const files = fflate.unzipSync(buf);
        const name = Object.keys(files)[0];
        const raw = files[name];
        delete files[name];
        // Ensure a tight ArrayBuffer (byteOffset 0) so absolute offsets are valid
        if (raw.byteOffset === 0 && raw.byteLength === raw.buffer.byteLength) return raw.buffer;
        return raw.slice().buffer;
      } catch (e) {
        if (isAllocationFailure(e)) throw new OutOfMemoryError("распаковку демо-записи");
        throw e;
      }
    }
    // GZIP?
    if (buf[0]===0x1F && buf[1]===0x8B) {
      onProgress && onProgress({ stage: "unpack", percent: null });
      try {
        const raw = fflate.gunzipSync(buf);
        if (raw.byteOffset === 0 && raw.byteLength === raw.buffer.byteLength) return raw.buffer;
        return raw.slice().buffer;
      } catch (e) {
        if (isAllocationFailure(e)) throw new OutOfMemoryError("распаковку демо-записи");
        throw e;
      }
    }
    return buf.buffer;
  }

  function fmtBytes(n) {
    if (n < 1024) return n + " Б";
    const units = ["КБ","МБ","ГБ"]; let v = n, i = -1;
    do { v /= 1024; i++; } while (v >= 1024 && i < units.length - 1);
    return v.toFixed(1) + " " + units[i];
  }

  window.__BaroReplay = { parseDemo, indexAtTime, decodeSnapshot, loadDemoBytes, OutOfMemoryError, fmtBytes, C, POS_SCALE };
})();

/* ═══ Main player application (memory-optimized) ═══ */
(function () {
  "use strict";
  const API = window.__BaroReplay;
  const C = API.C;
  const canvas = document.getElementById("rpvCanvas");
  const ctx = canvas.getContext("2d");
  const loadingEl = document.getElementById("rpvLoading");
  const loadingText = document.getElementById("rpvLoadingText");
  const loadingBarFill = document.getElementById("rpvLoadingBarFill");
  const loadingStages = document.getElementById("rpvLoadingStages");

  // React перезагружает этот скрипт с нуля при каждом открытии реплея (см.
  // player.tsx) — старый <script> тег снимается, но код в нём УЖЕ выполнился
  // и продолжает жить своими замыканиями: ничто, кроме этого флага, не
  // останавливает его requestAnimationFrame-цикл и загрузку. Если за это
  // время пользователь ушёл со страницы плеера совсем (не просто открыл
  // другой реплей — тогда #rpvCanvas и другие элементы переиспользуются),
  // цикл продолжал бы дёргать document.getElementById для узлов, которых
  // больше нет в DOM, и падал на "Cannot set properties of null" в loop().
  let destroyed = false;
  const globalListeners = []; // [target, type, handler] — снимаются в destroy()
  function onGlobal(target, type, handler, opts) {
    target.addEventListener(type, handler, opts);
    globalListeners.push([target, type, handler, opts]);
  }

  let demo = null;
  let cam = { x: 0, y: 0, zoom: 0.05 };
  let currentTimeMs = 0, playing = false, speed = 1, lastFrame = 0;
  let followSub = false, mainSubId = null;
  // Per-frame decoded snapshot cache (avoid re-decoding within one frame)
  let _curSnap = null, _curSnapIdx = -1;
  function snapAt(t){
    const idx = API.indexAtTime(demo.snaps, t);
    if (idx !== _curSnapIdx) { _curSnap = API.decodeSnapshot(demo.snaps, idx); _curSnapIdx = idx; }
    return _curSnap;
  }

  // ── Voice state ──────────────────────────────────────────────────────────
  let audioCtx = null, voiceEnabled = false, masterVol = 1.0;
  let spatial = true, falloffDist = 3000;
  let voicePtr = 0, opusSupported = false;
  const speakerState = new Map();   // sid -> {mute, vol}
  const speakingNow = new Set();    // sids speaking within window
  // Per-speaker Opus decoders (stateful codec — must NOT be shared between speakers)
  const decoders = new Map();       // sid -> {decoder, queue:[], gainNode}

  function resizeCanvas() {
    const r = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(r.width * dpr);
    canvas.height = Math.floor(r.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  onGlobal(window, "resize", resizeCanvas);
  function cw(){return canvas.width/(window.devicePixelRatio||1);}
  function ch(){return canvas.height/(window.devicePixelRatio||1);}
  function sx(wx){return cw()/2+(wx-cam.x)*cam.zoom;}
  function sy(wy){return ch()/2-(wy-cam.y)*cam.zoom;}
  function subPosAt(snap,sid){return(snap&&snap.subPos.get(sid))||{x:0,y:0};}

  function autoFit(){
    if(!demo||!demo.snaps.count)return;
    const snap=API.decodeSnapshot(demo.snaps,0);
    let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
    for(const e of snap.ents){minX=Math.min(minX,e.x);minY=Math.min(minY,e.y);maxX=Math.max(maxX,e.x);maxY=Math.max(maxY,e.y);}
    for(const h of demo.geometry.hulls){
      const sp=subPosAt(snap,h.subId);
      const x1=sp.x+h.x,y1=sp.y+h.y,x2=x1+h.w,y2=y1-h.h;
      minX=Math.min(minX,x1,x2);maxX=Math.max(maxX,x1,x2);
      minY=Math.min(minY,y1,y2);maxY=Math.max(maxY,y1,y2);
    }
    if(!isFinite(minX)){cam={x:0,y:0,zoom:0.05};return;}
    cam.x=(minX+maxX)/2;cam.y=(minY+maxY)/2;
    cam.zoom=Math.min(cw()/Math.max(1,maxX-minX),ch()/Math.max(1,maxY-minY))*0.8;
    cam.zoom=Math.max(0.005,Math.min(2,cam.zoom));
    const counts={};for(const h of demo.geometry.hulls)counts[h.subId]=(counts[h.subId]||0)+1;
    let best=null,bestN=-1;for(const k in counts)if(counts[k]>bestN){bestN=counts[k];best=parseInt(k);}
    mainSubId=best;
  }

  function teamColor(sp){
    if(!sp)return C.creature;if(sp.species!=="human")return C.creature;
    if(sp.teamId===1)return C.team1;if(sp.teamId===2)return C.team2;return C.team0;
  }
  function hex2rgb(h){h=h.replace("#","");return[parseInt(h.substr(0,2),16),parseInt(h.substr(2,2),16),parseInt(h.substr(4,2),16)];}
  function lerpColor(a,b,t){t=Math.max(0,Math.min(1,t));const pa=hex2rgb(a),pb=hex2rgb(b);return`rgb(${Math.round(pa[0]+(pb[0]-pa[0])*t)},${Math.round(pa[1]+(pb[1]-pa[1])*t)},${Math.round(pa[2]+(pb[2]-pa[2])*t)})`;}
  function hexA(h,a){const c=hex2rgb(h);return`rgba(${c[0]},${c[1]},${c[2]},${a})`;}

  function render(){
    const W=cw(),H=ch();ctx.fillStyle=C.bg;ctx.fillRect(0,0,W,H);
    if(!demo)return;
    const snap=snapAt(currentTimeMs);if(!snap)return;
    // World outline (compact flat polygons)
    ctx.strokeStyle=C.worldOutline;ctx.lineWidth=1;
    for(const poly of demo.world.polys){
      const n=poly.length;if(n<4)continue; // need >=2 points (4 ints)
      ctx.beginPath();
      ctx.moveTo(sx(poly[0]),sy(poly[1]));
      for(let i=2;i<n;i+=2)ctx.lineTo(sx(poly[i]),sy(poly[i+1]));
      ctx.closePath();ctx.stroke();
    }
    // Hulls + water
    for(const h of demo.geometry.hulls){
      const sp=subPosAt(snap,h.subId);
      const x1=sx(sp.x+h.x),y1=sy(sp.y+h.y),x2=sx(sp.x+h.x+h.w),y2=sy(sp.y+h.y-h.h);
      const left=Math.min(x1,x2),top=Math.min(y1,y2),rw=Math.abs(x2-x1),rh=Math.abs(y2-y1);
      if(left+rw<0||left>W||top+rh<0||top>H)continue;
      ctx.fillStyle=C.hullFill;ctx.fillRect(left,top,rw,rh);
      const wp=snap.water.get(h.id)||0;
      if(wp>0){ctx.fillStyle=C.water;const wh=rh*Math.min(1,wp);ctx.fillRect(left,top+rh-wh,rw,wh);}
      ctx.strokeStyle=C.hullOutline;ctx.lineWidth=1;ctx.strokeRect(left,top,rw,rh);
    }
    // Walls
    for(const wl of demo.geometry.walls){
      const sp=subPosAt(snap,wl.subId);
      const x1=sx(sp.x+wl.x),y1=sy(sp.y+wl.y),x2=sx(sp.x+wl.x+wl.w),y2=sy(sp.y+wl.y-wl.h);
      const left=Math.min(x1,x2),top=Math.min(y1,y2),rw=Math.abs(x2-x1),rh=Math.abs(y2-y1);
      if(left+rw<0||left>W||top+rh<0||top>H)continue;
      ctx.fillStyle=wl.platform?C.platform:C.wall;ctx.fillRect(left,top,Math.max(1,rw),Math.max(1,rh));
    }
    // Doors
    for(const d of demo.geometry.doors){
      const sp=subPosAt(snap,d.subId);const open=snap.doors.get(d.id)||0;
      const x1=sx(sp.x+d.x),y1=sy(sp.y+d.y),x2=sx(sp.x+d.x+d.w),y2=sy(sp.y+d.y-d.h);
      const left=Math.min(x1,x2),top=Math.min(y1,y2),rw=Math.abs(x2-x1),rh=Math.abs(y2-y1);
      const cf=1-open;ctx.fillStyle=lerpColor(C.doorOpen,C.doorClosed,cf);
      if(d.horizontal)ctx.fillRect(left,top,rw*cf,Math.max(1,rh));
      else ctx.fillRect(left,top,Math.max(1,rw),rh*cf);
    }

    // Characters
    for(const e of snap.ents){
      const sp=demo.spawns.get(e.id);const X=sx(e.x),Y=sy(e.y);
      if(X<-20||X>W+20||Y<-20||Y>H+20)continue;
      const dead=!!(e.flags&1),facingLeft=!!(e.flags&2),human=!!(e.flags&4);
      const sz=Math.max(4,8*cam.zoom*6);
      ctx.fillStyle=dead?C.dead:teamColor(sp);ctx.fillRect(X-sz/2,Y-sz/2,sz,sz);
      if(!dead){ctx.strokeStyle="#fff";ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(X,Y);ctx.lineTo(X+(facingLeft?-sz:sz),Y);ctx.stroke();}
      if(speakingNow.has(e.id)){ctx.strokeStyle="#22d06f";ctx.lineWidth=2;ctx.beginPath();ctx.arc(X,Y,sz,0,Math.PI*2);ctx.stroke();}
      if(sp&&sp.species==="human"&&!dead){
        const bw=sz*1.5,bh=2;ctx.fillStyle="#333";ctx.fillRect(X-bw/2,Y-sz/2-6,bw,bh);
        ctx.fillStyle=lerpColor("#5ADC5A","#DC4646",1-e.healthPct/200);ctx.fillRect(X-bw/2,Y-sz/2-6,bw*Math.max(0,Math.min(1,e.healthPct/200)),bh);
      }
      if(cam.zoom>0.02&&sp&&sp.name){ctx.fillStyle="#cdd6e0";ctx.font="10px sans-serif";ctx.textAlign="center";ctx.fillText(sp.name,X,Y-sz/2-9);}
    }
    // Combat events (windowed 700ms)
    for(const ev of demo.events){
      const dt=currentTimeMs-ev.timeMs;if(dt<0||dt>700)continue;
      const a=1-dt/700;const X=sx(ev.x),Y=sy(ev.y);
      if(ev.kind===10){ctx.fillStyle=hexA(C.damageFlash,a*0.7);const rad=Math.max(4,Math.min(40,ev.amount||10))*cam.zoom*4*(1+(1-a));ctx.beginPath();ctx.arc(X,Y,rad,0,Math.PI*2);ctx.fill();}
      else if(ev.kind===11){const subj=snap.ents.find(e2=>e2.id===ev.subjectId);if(subj){ctx.strokeStyle=hexA(C.shotLine,a);ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(sx(subj.x),sy(subj.y));ctx.lineTo(X,Y);ctx.stroke();}ctx.fillStyle=hexA(C.shotLine,a);ctx.beginPath();ctx.arc(X,Y,3,0,Math.PI*2);ctx.fill();}
    }
  }

  // ── Chat + Time ─────────────────────────────────────────────────────────
  let lastChatLen=-1;
  function updateChat(){
    if(!demo)return;const box=document.getElementById("rpvChat");if(!box)return;
    const visible=demo.chat.filter(c=>c.timeMs<=currentTimeMs).slice(-30);
    if(visible.length===lastChatLen)return;lastChatLen=visible.length;
    box.innerHTML=visible.map(c=>`<div class="rpv-chat-line"><b>${esc(c.author||"—")}</b>: ${esc(c.text||"")}</div>`).join("");
    box.scrollTop=box.scrollHeight;
  }
  function esc(t){const d=document.createElement("span");d.textContent=t;return d.innerHTML;}
  function fmtTime(ms){const s=Math.floor(ms/1000),m=Math.floor(s/60);return String(m).padStart(2,"0")+":"+String(s%60).padStart(2,"0");}
  // Наружу отдаём время не чаще ~4 раз в секунду: панель логов синхронизируется
  // по нему, а перерисовывать её на каждый кадр незачем.
  let lastTimeBroadcast = -1;
  function broadcastTime(){
    if(!demo)return;
    const bucket=Math.floor(currentTimeMs/250);
    if(bucket===lastTimeBroadcast)return;
    lastTimeBroadcast=bucket;
    window.dispatchEvent(new CustomEvent("baroreplay:time",{detail:{
      timeMs:currentTimeMs,durationMs:demo.durationMs,playing:playing,
    }}));
  }
  function updateTimeUI(){
    if(!demo)return;
    const label=document.getElementById("rpvTimeLabel");if(!label)return; // страница уже ушла со плеера
    label.textContent=fmtTime(currentTimeMs)+" / "+fmtTime(demo.durationMs);
    const seek=document.getElementById("rpvSeek");if(seek&&!seek.__dragging)seek.value=String(Math.round(currentTimeMs/demo.durationMs*1000)||0);
    broadcastTime();
  }
  function setPlaying(p){playing=p;const btn=document.getElementById("rpvPlay");if(btn)btn.textContent=p?"⏸":"▶";if(p&&audioCtx&&audioCtx.state==="suspended")audioCtx.resume();}

  // ── Voice playback (per-speaker Opus decoders, positional) ───────────────
  async function initAudio(){
    if(audioCtx)return;
    audioCtx=new(window.AudioContext||window.webkitAudioContext)({sampleRate:48000});
    opusSupported = !!window.AudioDecoder;
    if(!opusSupported)return;
    try {
      // Probe support
      const ok = await AudioDecoder.isConfigSupported({codec:"opus",sampleRate:48000,numberOfChannels:1});
      opusSupported = ok && ok.supported;
    } catch(e){ opusSupported = false; }
  }

  // Lazily create a dedicated decoder for a speaker.
  function getDecoder(sid){
    let d = decoders.get(sid);
    if (d) return d;
    if (!opusSupported) return null;
    const gainNode = audioCtx.createGain();
    gainNode.gain.value = 1;
    gainNode.connect(audioCtx.destination);
    const state = { decoder: null, gainNode, playhead: 0 };
    let decoder;
    try {
      decoder = new AudioDecoder({
        output: (frame) => {
          try {
            const n = frame.numberOfFrames;
            const sr = frame.sampleRate || 48000;
            const pcm = new Float32Array(n);
            frame.copyTo(pcm, {planeIndex:0, format:"f32-planar"});
            frame.close();
            const buf = audioCtx.createBuffer(1, n, sr);
            buf.getChannelData(0).set(pcm);
            const src = audioCtx.createBufferSource();
            src.buffer = buf;
            src.connect(gainNode);
            // Schedule sequentially on the audio clock to avoid overlap/gaps.
            const now = audioCtx.currentTime;
            const dur = n / sr;
            // If our playhead fell behind (gap in speech or first packet), resync
            // with a small latency cushion.
            if (state.playhead < now + 0.02) state.playhead = now + 0.08;
            src.start(state.playhead);
            state.playhead += dur;
          } catch(e){ /* ignore */ }
        },
        error: (e) => console.warn("Opus decode error (sid="+sid+"):", e),
      });
      decoder.configure({codec:"opus", sampleRate:48000, numberOfChannels:1});
    } catch(e){ console.warn("Decoder init failed:", e); return null; }
    state.decoder = decoder;
    decoders.set(sid, state);
    return state;
  }

  function resetDecoders(){
    for (const d of decoders.values()){
      try { d.decoder.close(); } catch(e){}
    }
    decoders.clear();
  }

  function applyGain(sid){
    const d = decoders.get(sid);
    if (d) d.gainNode.gain.value = computeVoiceGain(sid);
  }

  function computeVoiceGain(sid){
    const st=speakerState.get(sid);
    if(st&&st.mute)return 0;
    let gain=masterVol*(st?st.vol:1);
    if(spatial&&demo){
      const snap=snapAt(currentTimeMs);
      const ent=snap&&snap.ents.find(e=>e.id===sid);
      if(ent){const dx=ent.x-cam.x,dy=ent.y-cam.y;const dist=Math.sqrt(dx*dx+dy*dy);const att=Math.max(0,1-dist/falloffDist);gain*=att*att;}
    }
    return Math.max(0,Math.min(1,gain));
  }
  function sliceVoicePacket(i){
    return new Uint8Array(demo.voice.buffer, demo.voice.off[i], demo.voice.len[i]);
  }
  function voiceIndexAtTime(t){
    const V=demo.voice;let lo=0,hi=V.count-1,res=0;
    while(lo<=hi){const m=(lo+hi)>>1;if(V.t[m]<=t){res=m;lo=m+1;}else hi=m-1;}
    return res;
  }
  function updateVoice(prevTime){
    if(!voiceEnabled||!demo||!demo.voice.count)return;
    const V=demo.voice;
    // Seek backward / big jump → resync pointer and flush decoder states
    if(currentTimeMs<prevTime||Math.abs(currentTimeMs-prevTime)>1000){
      resetDecoders();
      voicePtr=voiceIndexAtTime(currentTimeMs);
      while(voicePtr<V.count&&V.t[voicePtr]<currentTimeMs)voicePtr++;
    }
    // Speaking indicators (±150ms)
    speakingNow.clear();
    const si=voiceIndexAtTime(currentTimeMs);
    for(let k=Math.max(0,si-30);k<Math.min(V.count,si+30);k++){
      if(Math.abs(V.t[k]-currentTimeMs)<=150)speakingNow.add(V.sid[k]);
    }
    // Schedule near-live packets to each speaker's own decoder
    while(voicePtr<V.count&&V.t[voicePtr]<=currentTimeMs){
      const i=voicePtr++;
      if(currentTimeMs-V.t[i]<=500){
        const sid=V.sid[i];
        const d=getDecoder(sid);
        if(d){
          d.gainNode.gain.value=computeVoiceGain(sid);
          try{
            const data=sliceVoicePacket(i);
            d.decoder.decode(new EncodedAudioChunk({type:"key",timestamp:V.t[i]*1000,data}));
          }catch(e){/*ignore*/}
        }
      }
    }
    // Continuously update spatial gain for active decoders
    for(const sid of decoders.keys()) applyGain(sid);
  }
  function updateSpeakingIndicators(){
    document.querySelectorAll(".rpv-player").forEach(row=>{
      const sid=parseInt(row.dataset.sid);
      row.classList.toggle("speaking",speakingNow.has(sid));
    });
  }

  // ── Loop ─────────────────────────────────────────────────────────────────
  function loop(ts){
    if(destroyed)return; // страница ушла со плеера — не трогаем DOM и не планируем следующий кадр
    const dt=lastFrame?(ts-lastFrame):0;lastFrame=ts;
    const prev=currentTimeMs;
    if(playing&&demo){currentTimeMs+=dt*speed;if(currentTimeMs>=demo.durationMs){currentTimeMs=demo.durationMs;setPlaying(false);}}
    if(followSub&&demo&&mainSubId!=null){const sp=subPosAt(snapAt(currentTimeMs),mainSubId);cam.x=sp.x;cam.y=sp.y;}
    render();updateChat();updateTimeUI();
    if(voiceEnabled){updateVoice(prev);updateSpeakingIndicators();}
    requestAnimationFrame(loop);
  }

  // ── Players panel (with per-speaker mute + volume) ────────────────────────
  function buildPlayersPanel(){
    const box=document.getElementById("rpvPlayers");
    if(!box)return;

    // Collect candidate speaker IDs from voice track (these are the ones that matter)
    const ids=new Set();
    if(demo.voice&&demo.voice.count){
      for(let i=0;i<demo.voice.count;i++)ids.add(demo.voice.sid[i]);
    }
    // Also include all spawned characters (so non-speaking players are listed too)
    for(const sp of demo.spawns.values())ids.add(sp.id);

    if(ids.size===0){
      box.innerHTML='<div style="color:var(--gray);font-size:12px;padding:8px;">Нет данных об игроках</div>';
      return;
    }

    box.innerHTML="";
    for(const id of ids){
      const sp=demo.spawns.get(id);
      const name=(sp&&sp.name)?sp.name:(sp&&sp.species?sp.species:("Игрок #"+id));
      const color=sp?teamColor(sp):C.team0;
      const st={mute:false,vol:1};speakerState.set(id,st);
      const row=document.createElement("div");
      row.className="rpv-player";row.dataset.sid=id;
      row.innerHTML=`<span class="rpv-pdot" style="background:${color}"></span>`+
        `<span class="rpv-pname" title="${esc(name)}">${esc(name)}</span>`+
        `<input type="range" class="rpv-pvol" min="0" max="100" value="100" title="Громкость">`+
        `<button class="rpv-pmute" title="Заглушить">🔊</button>`;
      const vol=row.querySelector(".rpv-pvol"),mute=row.querySelector(".rpv-pmute");
      vol.addEventListener("input",()=>{st.vol=vol.value/100;applyGain(id);});
      mute.addEventListener("click",()=>{st.mute=!st.mute;mute.textContent=st.mute?"🔇":"🔊";applyGain(id);});
      box.appendChild(row);
    }
  }

  // ── UI wiring ────────────────────────────────────────────────────────────
  function wireUI(){
    document.getElementById("rpvPlay").addEventListener("click",()=>setPlaying(!playing));
    document.querySelectorAll(".rpv-speed").forEach(btn=>{
      btn.addEventListener("click",()=>{speed=parseFloat(btn.dataset.speed);document.querySelectorAll(".rpv-speed").forEach(b=>b.classList.remove("active"));btn.classList.add("active");});
    });
    const seek=document.getElementById("rpvSeek");
    seek.addEventListener("input",()=>{seek.__dragging=true;if(demo)currentTimeMs=seek.value/1000*demo.durationMs;});
    seek.addEventListener("change",()=>{seek.__dragging=false;});
    document.getElementById("rpvFollow").addEventListener("click",(e)=>{followSub=!followSub;e.target.classList.toggle("active",followSub);});
    // Camera
    canvas.addEventListener("wheel",(e)=>{e.preventDefault();cam.zoom=Math.max(0.005,Math.min(2,cam.zoom*(e.deltaY<0?1.1:0.9)));},{passive:false});
    let dragging=false,lastX=0,lastY=0;
    canvas.addEventListener("mousedown",(e)=>{dragging=true;lastX=e.clientX;lastY=e.clientY;});
    onGlobal(window,"mouseup",()=>{dragging=false;});
    onGlobal(window,"mousemove",(e)=>{if(!dragging)return;cam.x-=(e.clientX-lastX)/cam.zoom;cam.y+=(e.clientY-lastY)/cam.zoom;lastX=e.clientX;lastY=e.clientY;followSub=false;document.getElementById("rpvFollow").classList.remove("active");});

    // Voice controls
    const ve=document.getElementById("rpvVoiceEnable");
    if(ve)ve.addEventListener("change",async(e)=>{
      voiceEnabled=e.target.checked;
      if(voiceEnabled){
        await initAudio();
        if(audioCtx.state==="suspended")await audioCtx.resume();
        if(!opusSupported)alert("Браузер не поддерживает декодирование Opus (WebCodecs AudioDecoder). Обновите Chrome/Edge.");
        voicePtr=voiceIndexAtTime(currentTimeMs);
      } else {
        resetDecoders();
      }
    });
    const mv=document.getElementById("rpvMasterVol");
    if(mv)mv.addEventListener("input",(e)=>{masterVol=e.target.value/100;});
    const sp=document.getElementById("rpvSpatial");
    if(sp)sp.addEventListener("change",(e)=>{spatial=e.target.checked;});
    const fo=document.getElementById("rpvFalloff");
    if(fo)fo.addEventListener("input",(e)=>{falloffDist=parseInt(e.target.value);});
  }

  // Мост для внешнего интерфейса (панель логов в React-обёртке страницы):
  // перемотка к моменту события и чтение текущего времени. Намеренно
  // минимальный — вся отрисовка и разбор демо остаются здесь.
  //
  // destroy() вызывается из cleanup-функции React-эффекта (player.tsx) ПЕРЕД
  // тем, как удалить <script>-теги этого инстанса и/или смонтировать новый —
  // без него старый requestAnimationFrame-цикл продолжал бы жить и падать,
  // как только пользователь уходит со страницы плеера совсем (см. loop()).
  window.__BaroReplayUI = {
    seekTo(ms){
      if(!demo)return;
      currentTimeMs=Math.max(0,Math.min(demo.durationMs,ms));
      updateTimeUI();
    },
    getTimeMs(){return currentTimeMs;},
    getDurationMs(){return demo?demo.durationMs:0;},
    setPlaying(p){setPlaying(!!p);},
    destroy(){
      if(destroyed)return;
      destroyed=true;
      playing=false;
      resetDecoders();
      if(audioCtx){try{audioCtx.close();}catch(e){/*ignore*/}audioCtx=null;}
      for(const [target,type,handler,opts] of globalListeners){
        try{target.removeEventListener(type,handler,opts);}catch(e){/*ignore*/}
      }
      globalListeners.length=0;
    },
  };

  // ── Загрузка: этапы и прогресс ──────────────────────────────────────────
  // Каждый этап отмечается в списке #rpvLoadingStages (если он есть в
  // разметке — старый статический шаблон его не содержит, поэтому все
  // обращения защищены) классами done/active, а полоса прогресса заполняется
  // только там, где реально известен процент (скачивание по Content-Length);
  // для распаковки/разбора точного процента нет — полоса просто держит своё
  // текущее положение, а крутится спиннер.
  function setStage(stage, detail){
    if(destroyed||!loadingText)return;
    if(loadingStages){
      let reached=false;
      for(const li of loadingStages.children){
        const key=li.dataset.stage;
        if(key===stage){li.classList.add("active");li.classList.remove("done");reached=true;}
        else if(!reached){li.classList.add("done");li.classList.remove("active");}
        else{li.classList.remove("active","done");}
      }
    }
    if(detail)loadingText.textContent=detail;
  }
  function setProgressBar(percent){
    if(destroyed||!loadingBarFill)return;
    if(percent==null){loadingBarFill.classList.add("indeterminate");return;}
    loadingBarFill.classList.remove("indeterminate");
    loadingBarFill.style.width=Math.max(0,Math.min(100,percent))+"%";
  }
  const STAGE_LABEL = {
    download: "Скачивание демо-записи",
    unpack: "Распаковка демо-записи",
    parse: "Разбор данных раунда",
    scene: "Подготовка сцены",
  };
  function onLoadProgress(p){
    if(destroyed)return;
    if(p.stage==="download"){
      const pct=p.percent;
      const size=p.total?` (${API.fmtBytes(p.bytes)} из ${API.fmtBytes(p.total)})`:(p.bytes?` (${API.fmtBytes(p.bytes)})`:"");
      setStage("download", STAGE_LABEL.download+(pct!=null?`: ${pct}%`:"…")+size);
      setProgressBar(pct);
    } else if(p.stage==="unpack"){
      setStage("unpack", STAGE_LABEL.unpack+"…");
      setProgressBar(null);
    }
  }

  // ── Boot ─────────────────────────────────────────────────────────────────
  async function boot(){
    resizeCanvas();wireUI();
    try{
      const ab=await API.loadDemoBytes(window.REPLAY_URL, onLoadProgress);
      if(destroyed)return; // ушли со страницы, пока файл качался/распаковывался

      setStage("parse", STAGE_LABEL.parse+"…");
      setProgressBar(null);
      // Разбор — синхронная функция без промежуточных await, отдать кадр
      // браузеру для отрисовки текста этапа перед тяжёлой работой:
      await new Promise(requestAnimationFrame);
      if(destroyed)return;
      demo=API.parseDemo(ab);
      // NOTE: ab (source buffer) is retained via demo.voice.buffer for lazy
      // opus packet slicing. This is intentional and far cheaper than copying
      // thousands of small packet arrays.

      setStage("scene", STAGE_LABEL.scene+"…");
      autoFit();buildPlayersPanel();
      if(destroyed)return;

      loadingEl.style.display="none";
      requestAnimationFrame(loop);
    }catch(e){
      if(destroyed)return; // страница уже ушла — некому показывать ошибку
      console.error(e);
      const friendly = e instanceof API.OutOfMemoryError
        ? e.message+". Файл слишком велик, чтобы браузер поместил его в память целиком. "
          +"Попробуйте закрыть другие вкладки, использовать 64-битный браузер "
          +"с бо́льшим объёмом ОЗУ, либо посмотреть эту запись на другом устройстве."
        : "Ошибка: "+e.message;
      loadingEl.innerHTML='<div class="rpv-err">'+esc(friendly)+'</div>';
    }
  }
  // Classic full-page load: DOMContentLoaded hasn't fired yet, wait for it.
  // Dynamically injected (SPA route): the document is already "complete" by
  // the time this script runs, so DOMContentLoaded already fired and would
  // never fire again — boot immediately in that case instead.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
