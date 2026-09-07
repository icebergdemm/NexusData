/* Barotrauma .barodemo web replay player (memory-optimized).
 * Key optimizations vs naive approach:
 * 1. Voice data is NOT stored in memory — skipped during parse, decoded on-demand
 * 2. World polygons stored as flat Int32Array (compact, no per-point objects)
 * 3. Snapshot entities stored as compact typed arrays
 * 4. File is never held as ONE giant ArrayBuffer — see PagedBuffer below
 * 5. Download + decompression are fully streamed, chunk by chunk
 */
(function replayScriptGenerationGuard() {
  "use strict";

  // React attaches an instance id to dynamically injected player scripts.
  // A detached <script> can still execute after navigation, so it must prove
  // that its owning effect is current before reading #rpvCanvas or starting a
  // second download/animation loop. The classic server-rendered page has no
  // id and intentionally follows the historical path.
  const replayInstanceId = document.currentScript?.dataset?.replayInstanceId || null;
  if (replayInstanceId && window.__BARO_REPLAY_ACTIVE_INSTANCE__ !== replayInstanceId) return;

  // Shared by the parser and the sibling playback/UI IIFE below. Keeping this
  // in the outer player-instance scope is important: a declaration inside
  // parseDemo() or the parser IIFE leaves broadcastTime()/buildPlayersPanel()
  // with an unresolved identifier and crashes only after a real replay parses.
  const collectTimeline = document.documentElement.dataset.uiVersion === "v2";
  const isHumanSpecies = (species) => typeof species === "string" && species.toLowerCase() === "human";

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

  /** Ошибка "не хватило памяти на буфер" (RangeError из new ArrayBuffer/
   * Uint8Array, либо DOMException QuotaExceededError). PagedBuffer держит
   * страницы фиксированного маленького размера (см. ниже), так что это
   * теперь означает по-настоящему нехватку памяти в системе, а не
   * упирание в потолок одного JS-объекта — раньше сообщение об этой
   * ошибке было буквально неправдой на любой современной машине. */
  class OutOfMemoryError extends Error {
    constructor(stage) { super(`Не хватило памяти браузера на ${stage}`); this.stage = stage; }
  }
  function isAllocationFailure(e) {
    return e instanceof RangeError || e?.name === "QuotaExceededError" ||
      /allocation failed|array buffer/i.test(e?.message || "");
  }

  // ── PagedBuffer ───────────────────────────────────────────────────────────
  // Демо-файлы бывают гигабайты после распаковки (долгий раунд с позициями
  // на 10 Гц сжимается в разы, а живая речь — почти нет). Чтение или
  // распаковка ВСЕГО файла в ОДИН ArrayBuffer падает с "Array buffer
  // allocation failed" не из-за нехватки реальной памяти на диске/в ОЗУ —
  // движок JS ограничивает размер ОДНОГО буфера сам по себе, независимо от
  // того, сколько всего свободно. PagedBuffer копит данные вместо этого в
  // массиве буферов по PAGE_SIZE байт каждый — ни одна отдельная аллокация
  // никогда не приближается к этому потолку, сколько бы всего памяти ни
  // потребовалось суммарно.
  //
  // Публичный API (u8/u16/i16/u32/i32/f32/i64/slice) принимает АБСОЛЮТНОЕ
  // смещение от начала логического потока байт и сам находит нужную
  // страницу — почти все чтения попадают в одну страницу (быстрый путь,
  // без копирования); значение, пересекающее границу страницы (редкость —
  // страница держит десятки мегабайт, а любое поле формата — единицы-сотни
  // байт), собирается через маленький временный буфер.
  const PAGE_SIZE = 4 * 1024 * 1024; // Smaller pages reduce idle RAM for short demos.

  function readU16(dv, o) { return dv.getUint16(o, true); }
  function readI16(dv, o) { return dv.getInt16(o, true); }
  function readU32(dv, o) { return dv.getUint32(o, true); }
  function readI32(dv, o) { return dv.getInt32(o, true); }
  function readF32(dv, o) { return dv.getFloat32(o, true); }
  function readI64(dv, o) { return Number(dv.getBigInt64(o, true)); }

  class PagedBuffer {
    constructor() {
      this.pages = [];   // Uint8Array[]
      this._views = [];  // DataView[], лениво создаются по странице
      this.length = 0;   // логический размер всего потока
      this._cur = null;
      this._curLen = 0;
    }
    _newPage() {
      let page;
      try { page = new Uint8Array(PAGE_SIZE); }
      catch (e) { throw isAllocationFailure(e) ? new OutOfMemoryError("распаковку демо-записи") : e; }
      this._cur = page; this._curLen = 0;
      this.pages.push(page); this._views.push(null);
    }
    /** Дописать очередной кусок байт (произвольной длины — границы кусков
     * из сети/распаковщика никак не связаны с границами наших страниц). */
    append(chunk) {
      let off = 0;
      while (off < chunk.length) {
        if (!this._cur || this._curLen >= PAGE_SIZE) this._newPage();
        const n = Math.min(PAGE_SIZE - this._curLen, chunk.length - off);
        this._cur.set(chunk.subarray(off, off + n), this._curLen);
        this._curLen += n; off += n; this.length += n;
      }
    }
    _loc(off) { return [(off / PAGE_SIZE) | 0, off % PAGE_SIZE]; }
    _view(p) {
      let v = this._views[p];
      if (!v) { const pg = this.pages[p]; v = new DataView(pg.buffer, pg.byteOffset, pg.byteLength); this._views[p] = v; }
      return v;
    }
    u8(off) { const [p, w] = this._loc(off); return this.pages[p][w]; }
    i8(off) { const v = this.u8(off); return v > 127 ? v - 256 : v; }
    _prim(off, size, read) {
      const [p, w] = this._loc(off);
      if (w + size <= PAGE_SIZE) return read(this._view(p), w);
      const scratch = new Uint8Array(size); // редкий случай значения на стыке страниц
      for (let k = 0; k < size; k++) scratch[k] = this.u8(off + k);
      return read(new DataView(scratch.buffer), 0);
    }
    u16(off) { return this._prim(off, 2, readU16); }
    i16(off) { return this._prim(off, 2, readI16); }
    u32(off) { return this._prim(off, 4, readU32); }
    i32(off) { return this._prim(off, 4, readI32); }
    f32(off) { return this._prim(off, 4, readF32); }
    i64(off) { return this._prim(off, 8, readI64); }
    /** Uint8Array на диапазон [off, off+len) — вид без копирования, если он
     * целиком в одной странице (обычный случай), иначе — копия. */
    slice(off, len) {
      const [p, w] = this._loc(off);
      if (w + len <= PAGE_SIZE) return this.pages[p].subarray(w, w + len);
      const out = new Uint8Array(len);
      for (let k = 0; k < len; k++) out[k] = this.u8(off + k);
      return out;
    }
  }

  class Reader {
    constructor(pb) { this.pb = pb; this.o = 0; this.len = pb.length; }
    u8() { const v = this.pb.u8(this.o); this.o += 1; return v; }
    i8() { const v = this.pb.i8(this.o); this.o += 1; return v; }
    u16() { const v = this.pb.u16(this.o); this.o += 2; return v; }
    i16() { const v = this.pb.i16(this.o); this.o += 2; return v; }
    u32() { const v = this.pb.u32(this.o); this.o += 4; return v; }
    i32() { const v = this.pb.i32(this.o); this.o += 4; return v; }
    f32() { const v = this.pb.f32(this.o); this.o += 4; return v; }
    i64() { const v = this.pb.i64(this.o); this.o += 8; return v; }
    skip(n) { this.o += n; }
    str() {
      const n = this.u16();
      if (n > 8192 || this.o + n > this.len) throw new Error("Строка вне границ (len=" + n + ") — файл повреждён или несовместим");
      const b = this.pb.slice(this.o, n); this.o += n;
      return new TextDecoder("utf-8").decode(b);
    }
    need(n) { if (this.o + n > this.len) throw new Error("Чтение за границей файла"); }
  }

  function parseDemo(pagedBuffer) {
    const r = new Reader(pagedBuffer);
    if (r.u8()!==66||r.u8()!==68||r.u8()!==77||r.u8()!==79)
      throw new Error("Неверная сигнатура файла (не .barodemo)");
    const header = {
      version: r.u16(), flags: r.u16(), tickRate: r.u8(), startUnixMs: r.i64(),
      serverName: r.str(), levelSeed: r.str(), levelName: r.str(), subName: r.str(),
    };
    r.u32();
    const hasVoice = (header.flags & 1) !== 0;
    if (header.version !== 4 && header.version !== 5) {
      throw new Error("Версия формата " + header.version + " не поддерживается (ожидается 4 или 5). Файл может быть повреждён.");
    }
    const geometry = { hulls: [], walls: [], doors: [] };
    const world = { w: 0, h: 0, polys: [] };
    const chat = [], events = [], spawnEvents = [], healthDrops = [];
    const spawns = new Map();
    const activeSpawns = new Map();
    const sampledHealth = new Map();
    const healthSampleIntervalMs = Math.max(50, Math.round(1000 / Math.max(1, header.tickRate || 10)));
    let nextHealthSampleMs = 0;
    let origin = { x: 0, y: 0 }, durationMs = 0;
    // Lazy snapshot index: parallel arrays (time, byte-offset, origin at that point)
    const snapTimes = [], snapOffsets = [], snapOx = [], snapOy = [];
    const inventories = [];
    // Voice: collect compact offset records (no per-packet Uint8Array allocations)
    const voiceT = [], voiceSid = [], voiceOff = [], voiceLen = [], voiceRadio = [];
    let lastSnapshotTime = 0;

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
      else if (type === 0x03) {
        const t=r.u32(),id=r.u16(),species=r.str(),name=r.str(),teamId=r.i8();
        const spawn={id,species,name,teamId};spawns.set(id,spawn);activeSpawns.set(id,spawn);sampledHealth.delete(id);
        if(collectTimeline)spawnEvents.push({timeMs:t,kind:"spawn",...spawn});
        if(t>durationMs)durationMs=t;
      }
      else if (type === 0x04) {
        const t=r.u32(),id=r.u16(),known=collectTimeline?activeSpawns.get(id):null;
        if(collectTimeline)spawnEvents.push({timeMs:t,kind:"despawn",id,species:known?.species||"",name:known?.name||"",teamId:known?.teamId??0});
        activeSpawns.delete(id);sampledHealth.delete(id);
        if(t>durationMs)durationMs=t;
      }
      else if (type === 0x01) { // SNAPSHOT — INDEX ONLY (lazy decode at render)
        const snapStart = r.o - 1; // include the frame-type byte
        const timeMs=r.u32(); const ec=r.u16();
        lastSnapshotTime = timeMs;
        if(ec>LIM.ent)throw new Error("Битый счётчик entities="+ec+" — рассинхрон парсера");
        if(collectTimeline&&timeMs>=nextHealthSampleMs){
          // Real recordings repeat every active affliction as damage telemetry.
          // Sample the authoritative health byte at the advertised tick rate;
          // later only a real health decrease can become an audit event.
          for(let i=0;i<ec;i++){
            const id=r.u16();r.skip(4);r.u8();const healthByte=r.u8();
            const spawn=activeSpawns.get(id);
            if(!isHumanSpecies(spawn?.species))continue;
            const previous=sampledHealth.get(id);
            if(previous!==undefined&&healthByte<previous){
              healthDrops.push({
                timeMs,victimId:id,victimName:spawn.name||"",
                healthLossPct:(previous-healthByte)*200/255,
              });
            }
            sampledHealth.set(id,healthByte);
          }
          nextHealthSampleMs=timeMs+healthSampleIntervalMs;
        }else{
          r.skip(ec*8); // entity = u16 id + i16 x + i16 y + u8 flags + u8 health = 8 bytes
        }
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
      } else if (type === 0x07) { // VOICE — record offset, keep bytes in the paged buffer
        const t=r.u32(); const sid=r.u16(); const len=r.u16();
        voiceT.push(t); voiceSid.push(sid); voiceRadio.push(0); voiceOff.push(r.o); voiceLen.push(len);
        r.skip(len); // do not copy now — slice lazily during playback
      } else if (type === 0x09) { // VOICE WITH CHANNEL: 0 local, 1 radio
        const t=r.u32(); const sid=r.u16(); const radio=r.u8(); const len=r.u16();
        voiceT.push(t); voiceSid.push(sid); voiceRadio.push(radio ? 1 : 0); voiceOff.push(r.o); voiceLen.push(len);
        r.skip(len);
      } else if (type === 0x08) { // INVENTORY — indexed state, not needed for map rendering
        const count = r.u16();
        if (count > 65535) throw new Error("Битый счётчик inventory=" + count);
        const items = [];
        for (let i = 0; i < count; i++) {
          items.push({id:r.u16(), ownerId:r.u16(), identifier:r.str(), condition:r.u8()});
        }
        inventories.push({timeMs:lastSnapshotTime,items});
      } else if (type === 0xFF) { const t=r.u32();if(t>durationMs)durationMs=t;break; }
      else { throw new Error("Неизвестный тип фрейма 0x"+type.toString(16)+" — формат несовместим или файл повреждён"); }
    }
    // Build compact voice index. We keep ONE reference to the paged buffer;
    // packets are sliced on demand (small, short-lived) during playback.
    const voice = {
      count: voiceT.length,
      t: Int32Array.from(voiceT),
      sid: Uint16Array.from(voiceSid),
      radio: Uint8Array.from(voiceRadio),
      // Файл теперь может быть сколь угодно большим (PagedBuffer больше не
      // упирается в потолок одного ArrayBuffer — см. выше), а voice-пакеты
      // разбросаны по всему файлу и попадаются позже снапшотов. Int32Array
      // хранит смещения знаковыми 32-битными числами — на файле больше
      // 2^31 байт (2 ГБ) это молча переполнилось бы и испортило смещение
      // (не бросив ошибку сразу, а тихо подсунув чтение из другого места
      // файла при воспроизведении голоса). Тот же переход на Float64Array
      // при больших смещениях, что уже был у снапшотов ниже.
      off: (voiceOff.length && voiceOff[voiceOff.length-1] < 2**31) ? Int32Array.from(voiceOff) : Float64Array.from(voiceOff),
      len: Uint16Array.from(voiceLen),
      store: pagedBuffer,
    };
    const snaps = {
      count: snapTimes.length,
      t: Int32Array.from(snapTimes),
      off: (snapOffsets.length && snapOffsets[snapOffsets.length-1] < 2**31) ? Int32Array.from(snapOffsets) : Float64Array.from(snapOffsets),
      ox: Int32Array.from(snapOx),
      oy: Int32Array.from(snapOy),
      store: pagedBuffer,
    };
    return {header,hasVoice,geometry,world,snaps,chat,events,spawns,spawnEvents,healthDrops,inventories,durationMs,voice};
  }

  function indexAtTime(snaps, t) {
    let lo=0,hi=snaps.count-1,res=0;
    while(lo<=hi){const m=(lo+hi)>>1;if(snaps.t[m]<=t){res=m;lo=m+1;}else hi=m-1;}
    return res;
  }

  // Decode a single snapshot on demand from the paged buffer.
  // Returns a transient object (only one is alive at a time during render).
  function decodeSnapshot(snaps, idx) {
    if (idx < 0 || idx >= snaps.count) return null;
    const s = snaps.store;
    let o = snaps.off[idx];
    const ox = snaps.ox[idx], oy = snaps.oy[idx];
    o += 1; // skip frame-type byte (0x01)
    const timeMs = s.u32(o); o += 4;
    const ec = s.u16(o); o += 2;
    const ents = new Array(ec);
    for (let i=0;i<ec;i++){
      const id=s.u16(o);o+=2;
      const px=s.i16(o);o+=2;
      const py=s.i16(o);o+=2;
      const fl=s.u8(o);o+=1;
      const hb=s.u8(o);o+=1;
      ents[i]={id,x:ox+px*POS_SCALE,y:oy+py*POS_SCALE,flags:fl,healthPct:hb*200/255};
    }
    const doors=new Map();const ddc=s.u16(o);o+=2;
    for(let i=0;i<ddc;i++){const id=s.u16(o);o+=2;doors.set(id,s.u8(o)/255);o+=1;}
    const water=new Map();const hwc=s.u16(o);o+=2;
    for(let i=0;i<hwc;i++){const id=s.u16(o);o+=2;water.set(id,s.u8(o)/255);o+=1;}
    const subPos=new Map();const spc=s.u16(o);o+=2;
    for(let i=0;i<spc;i++){const sid=s.u16(o);o+=2;const x=s.i32(o);o+=4;const y=s.i32(o);o+=4;subPos.set(sid,{x,y});}
    return {timeMs,ents,doors,water,subPos};
  }

  function fmtBytes(n) {
    if (n < 1024) return n + " Б";
    const units = ["КБ","МБ","ГБ"]; let v = n, i = -1;
    do { v /= 1024; i++; } while (v >= 1024 && i < units.length - 1);
    return v.toFixed(1) + " " + units[i];
  }

  /** Скачивание + распаковка демо-записи ПОЛНОСТЬЮ потоковые: ни сжатые
   * байты, ни распакованные никогда не собираются в один буфер целиком —
   * каждый пришедший из сети кусок сразу уходит в потоковый распаковщик
   * (fflate.Unzip/Gunzip), а тот сразу дописывает результат в PagedBuffer
   * постранично. Раньше здесь сначала копился весь скачанный файл в один
   * Uint8Array, потом fflate.unzipSync разворачивал его в ещё один — на
   * 100-мегабайтном .zip с сильно сжимаемыми (повторяющимися) данными раунда
   * распакованный .barodemo мог оказаться в разы, а то и на порядок больше
   * (позиции/снапшоты на 10 Гц — исключительно избыточные данные), и именно
   * ЭТОТ буфер утыкался в потолок размера одного ArrayBuffer в движке —
   * независимо от того, сколько реальной оперативной памяти свободно.
   */
  async function loadDemoBytes(url, onProgress, signal) {
    if (signal?.aborted) throw new DOMException("The operation was aborted", "AbortError");
    onProgress && onProgress({ stage: "download", percent: 0 });
    const resp = await fetch(url, { signal });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const total = parseInt(resp.headers.get("content-length") || "0");
    const reader = resp.body.getReader();

    const store = new PagedBuffer();
    let received = 0;
    let format = null; // "zip" | "gzip" | "raw" — определяется по первым байтам потока
    let sniff = [], sniffLen = 0; // копим байты, пока не наберём достаточно для сигнатуры
    let unzipper = null, gunzip = null;
    let streamError = null;

    // ВАЖНО: здесь и ниже ошибка НЕ переклассифицируется в OutOfMemoryError
    // по одному лишь instanceof RangeError. Такая переклассификация уже
    // однажды маскировала настоящую причину: любой RangeError откуда угодно
    // из недр push()/inflate (в т.ч. реальный баг, не имеющий отношения к
    // памяти) подписывался как "не хватило памяти браузера", и по такому
    // сообщению нельзя было понять, что чинить. OutOfMemoryError бросает
    // ТОЛЬКО PagedBuffer._newPage() — единственное место, где ошибка
    // действительно означает именно нехватку памяти на аллокацию одной
    // 32-мегабайтной страницы; если она произошла, то и долетит сюда уже
    // готовым экземпляром OutOfMemoryError естественным путём — переносить
    // её вручную не нужно. Любая другая ошибка сохраняет свой настоящий
    // тип/сообщение/стек.
    function routeToDecoder(chunk, final) {
      try {
        if (format === "zip") unzipper.push(chunk, final);
        else if (format === "gzip") gunzip.push(chunk, final);
        else store.append(chunk); // "raw" — несжатый .barodemo, как есть
      } catch (e) {
        streamError = e;
      }
    }
    function decide(chunk, final) {
      if (format) { routeToDecoder(chunk, final); return; }
      sniff.push(chunk); sniffLen += chunk.length;
      if (sniffLen < 4 && !final) return; // сигнатура ещё не вся пришла — ждём следующий кусок
      const head = new Uint8Array(sniffLen);
      let o = 0; for (const c of sniff) { head.set(c, o); o += c.length; }
      sniff = null;
      if (head[0]===0x50 && head[1]===0x4B && head[2]===0x03 && head[3]===0x04) {
        format = "zip";
        unzipper = new fflate.Unzip((file) => {
          file.ondata = (err, data) => {
            if (err) { streamError = err; return; }
            try { store.append(data); }
            catch (e) { streamError = e; }
          };
          file.start();
        });
        unzipper.register(fflate.UnzipInflate);
        routeToDecoder(head, final);
      } else if (head[0]===0x1F && head[1]===0x8B) {
        format = "gzip";
        gunzip = new fflate.Gunzip((data) => {
          try { store.append(data); }
          catch (e) { streamError = e; }
        });
        routeToDecoder(head, final);
      } else {
        format = "raw";
        routeToDecoder(head, final);
      }
    }

    while (true) {
      if (signal?.aborted) throw new DOMException("The operation was aborted", "AbortError");
      const { done, value } = await reader.read();
      if (done) break;
      received += value.length;
      if (onProgress) onProgress({ stage: "download", percent: total ? Math.round(received/total*100) : null, bytes: received, total });
      decide(value, false);
      if (streamError) throw streamError;
    }
    if (signal?.aborted) throw new DOMException("The operation was aborted", "AbortError");
    // Этап "распаковка" объявляется ЗДЕСЬ, а не в момент распознавания
    // сигнатуры. Распаковка технически идёт параллельно скачиванию (каждый
    // кусок сразу уходит в распаковщик), но сигнатура распознаётся уже по
    // ПЕРВЫМ 4 БАЙТАМ — объявляя этап там, интерфейс отмечал скачивание
    // завершённым в самом его начале и весь остаток показывал "Распаковка"
    // с бегущей полосой, одновременно печатая "Скачивание: 12%" в строке
    // состояния. Здесь же скачивание действительно закончено, а распаковке
    // осталось добить хвост — обе надписи говорят правду.
    if (onProgress) onProgress({ stage: "unpack", percent: null });
    // Явный завершающий пустой кусок с final=true — сигнал потоковым
    // распаковщикам "это всё, посчитайте контрольные суммы и добейте выход"
    // (проверено: fflate.Unzip/Gunzip одинаково корректно принимают такой
    // отдельный финальный кусок и final=true на последнем куске с данными).
    decide(new Uint8Array(0), true);
    if (streamError) throw streamError;
    if (format === "zip" && !store.length && received) {
      // Пустое имя файла в zip/распаковщик так и не вызвал onfile — либо
      // архив пуст, либо повреждён; отдельно, чтобы не путать с "файл и
      // правда нулевого размера" (тогда received тоже 0).
      throw new Error("Не удалось найти файл внутри .zip — архив повреждён или пуст");
    }
    return store;
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
  const loadController = new AbortController();
  const globalListeners = []; // [target, type, handler] — снимаются в destroy()
  function onGlobal(target, type, handler, opts) {
    target.addEventListener(type, handler, opts);
    globalListeners.push([target, type, handler, opts]);
  }

  let demo = null;
  let cam = { x: 0, y: 0, zoom: 0.05 };
  let currentTimeMs = 0, playing = false, speed = 1, lastFrame = 0;
  let seekWindow = null;
  let followSub = false, followSubId = null;
  // Локальные габариты каждой структуры (подлодка, станция, обломки), считаются
  // один раз по геометрии. В геометрии координаты относительные — мировые
  // получаются прибавлением subPos из снапшота.
  let subCenters = null;   // subId -> {cx,cy} — центр в локальных координатах
  // Per-frame decoded snapshot cache (avoid re-decoding within one frame)
  let _curSnap = null, _curSnapIdx = -1;
  function snapAt(t){
    const idx = API.indexAtTime(demo.snaps, t);
    if (idx !== _curSnapIdx) { _curSnap = API.decodeSnapshot(demo.snaps, idx); _curSnapIdx = idx; }
    return _curSnap;
  }
  let _nextSnap = null, _nextSnapIdx = -1;
  function snapAtInterpolated(t){
    const idx=API.indexAtTime(demo.snaps,t);
    const current=snapAt(t);
    if(!current||idx>=demo.snaps.count-1)return current;
    if(_nextSnapIdx!==idx+1){_nextSnap=API.decodeSnapshot(demo.snaps,idx+1);_nextSnapIdx=idx+1;}
    if(!_nextSnap)return current;
    const span=Math.max(1,_nextSnap.timeMs-current.timeMs);
    const alpha=Math.max(0,Math.min(1,(t-current.timeMs)/span));
    const nextEntities=new Map(_nextSnap.ents.map(e=>[e.id,e]));
    const ents=current.ents.map(e=>{
      const n=nextEntities.get(e.id);if(!n)return e;
      return {...e,x:e.x+(n.x-e.x)*alpha,y:e.y+(n.y-e.y)*alpha,healthPct:e.healthPct+(n.healthPct-e.healthPct)*alpha};
    });
    const subPos=new Map();
    for(const [id,p] of current.subPos){const n=_nextSnap.subPos.get(id);subPos.set(id,n?{x:p.x+(n.x-p.x)*alpha,y:p.y+(n.y-p.y)*alpha}:p);}
    const doors=new Map();
    for(const [id,p] of current.doors){const n=_nextSnap.doors.get(id);doors.set(id,n===undefined?p:p+(n-p)*alpha);}
    return {...current,ents,subPos,doors};
  }

  // ── Voice state ──────────────────────────────────────────────────────────
  let audioCtx = null, voiceEnabled = false, masterVol = 1.0;
  let spatial = true, falloffDist = 3000;
  let voicePtr = 0, opusSupported = false;
  const speakerState = new Map();   // sid -> {mute, vol}
  const speakingNow = new Set();    // sids speaking within window
  const speakingRadio = new Set();  // sids currently using the radio channel
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

  function buildSubCenters(){
    const acc=new Map();
    const add=(sid,x1,y1,x2,y2)=>{
      let b=acc.get(sid);
      if(!b){b={minX:Infinity,minY:Infinity,maxX:-Infinity,maxY:-Infinity};acc.set(sid,b);}
      b.minX=Math.min(b.minX,x1,x2);b.maxX=Math.max(b.maxX,x1,x2);
      b.minY=Math.min(b.minY,y1,y2);b.maxY=Math.max(b.maxY,y1,y2);
    };
    for(const h of demo.geometry.hulls)add(h.subId,h.x,h.y,h.x+h.w,h.y-h.h);
    for(const w of demo.geometry.walls)add(w.subId,w.x,w.y,w.x+w.w,w.y-w.h);
    subCenters=new Map();
    for(const[sid,b]of acc)subCenters.set(sid,{cx:(b.minX+b.maxX)/2,cy:(b.minY+b.maxY)/2});
  }
  function subCenterAt(snap,sid){
    const sp=subPosAt(snap,sid),c=subCenters&&subCenters.get(sid);
    return{x:sp.x+(c?c.cx:0),y:sp.y+(c?c.cy:0)};
  }
  // Цель слежения выбирается по тому, на что пользователь сейчас смотрит:
  // берётся структура, чей центр ближе всего к центру камеры. Раньше цель была
  // жёстко привязана к структуре с наибольшим числом hull-ов, то есть почти
  // всегда к главной подлодке — и режим «Следить» отдёргивал камеру от станции
  // или обломков, которые пользователь как раз и разглядывал.
  function pickFollowTarget(){
    if(!demo)return null;
    if(!subCenters)buildSubCenters();
    const snap=snapAt(currentTimeMs);
    let best=null,bestD=Infinity;
    for(const sid of subCenters.keys()){
      const c=subCenterAt(snap,sid);
      const dx=c.x-cam.x,dy=c.y-cam.y,d=dx*dx+dy*dy;
      if(d<bestD){bestD=d;best=sid;}
    }
    return best;
  }

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
    buildSubCenters();
  }

  function teamColor(sp){
    if(!sp)return C.creature;if(!isHumanSpecies(sp.species))return C.creature;
    if(sp.teamId===1)return C.team1;if(sp.teamId===2)return C.team2;return C.team0;
  }
  function hex2rgb(h){h=h.replace("#","");return[parseInt(h.substr(0,2),16),parseInt(h.substr(2,2),16),parseInt(h.substr(4,2),16)];}
  function lerpColor(a,b,t){t=Math.max(0,Math.min(1,t));const pa=hex2rgb(a),pb=hex2rgb(b);return`rgb(${Math.round(pa[0]+(pb[0]-pa[0])*t)},${Math.round(pa[1]+(pb[1]-pa[1])*t)},${Math.round(pa[2]+(pb[2]-pa[2])*t)})`;}
  function hexA(h,a){const c=hex2rgb(h);return`rgba(${c[0]},${c[1]},${c[2]},${a})`;}

  function render(){
    const W=cw(),H=ch();ctx.fillStyle=C.bg;ctx.fillRect(0,0,W,H);
    if(!demo)return;
    const snap=snapAtInterpolated(currentTimeMs);if(!snap)return;
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
      if(speakingNow.has(e.id)){ctx.strokeStyle=speakingRadio.has(e.id)?"#ffd23f":"#63d9f2";ctx.lineWidth=2;ctx.beginPath();ctx.arc(X,Y,sz,0,Math.PI*2);ctx.stroke();}
      if(sp&&isHumanSpecies(sp.species)&&!dead){
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
  let lastInventoryBucket=-1, lastRadioBucket=-1;
  function inventoryAt(t){
    const list=demo?.inventories||[];let result=null;
    for(const frame of list){if(frame.timeMs<=t)result=frame;else break;}
    return result;
  }
  function updateInventory(){
    if(!demo)return;
    const bucket=Math.floor(currentTimeMs/150);if(bucket===lastInventoryBucket)return;lastInventoryBucket=bucket;
    const box=document.getElementById("rpvInventory");if(!box)return;
    const frame=inventoryAt(currentTimeMs);
    if(!frame||!frame.items.length){box.innerHTML='<span style="color:var(--gray);">Инвентари пусты</span>';return;}
    const groups=new Map();
    for(const item of frame.items){if(!item.ownerId)continue;if(!groups.has(item.ownerId))groups.set(item.ownerId,[]);groups.get(item.ownerId).push(item);}
    box.innerHTML=[...groups].map(([id,items])=>{
      const sp=demo.spawns.get(id);const name=esc(sp?.name||("Игрок #"+id));
      return '<div class="rpv-inventory-player">'+name+'</div>'+items.map(i=>
        '<div class="rpv-inventory-item">'+esc(i.identifier||"?")+" · "+Math.round(i.condition*100/255)+"%</div>").join("");
    }).join("")||'<span style="color:var(--gray);">Инвентари пусты</span>';
  }
  function updateRadioPanel(){
    if(!demo||!demo.voice)return;
    const bucket=Math.floor(currentTimeMs/120);if(bucket===lastRadioBucket)return;lastRadioBucket=bucket;
    const box=document.getElementById("rpvRadio");if(!box)return;
    const from=currentTimeMs-2500, seen=new Set(), rows=[];
    for(let i=demo.voice.count-1;i>=0;i--){
      const t=demo.voice.t[i];if(t>currentTimeMs)continue;if(t<from)break;
      const sid=demo.voice.sid[i], radio=!!demo.voice.radio[i], key=sid+":"+radio;if(seen.has(key))continue;seen.add(key);
      const sp=demo.spawns.get(sid);rows.push('<div class="rpv-radio-line '+(radio?"":"local")+'">'+
        (radio?"📻 Радиоэфир":"◌ Локальный")+" · "+esc(sp?.name||("Игрок #"+sid))+" · общий канал</div>");
    }
    box.innerHTML=rows.length?rows.reverse().join(""):'<span style="color:var(--gray);">Нет голосовой активности</span>';
  }
  function esc(t){const d=document.createElement("span");d.textContent=t;return d.innerHTML;}
  function fmtTime(ms){const s=Math.floor(ms/1000),m=Math.floor(s/60);return String(m).padStart(2,"0")+":"+String(s%60).padStart(2,"0");}
  function fmtExactTime(ms){
    const safe=Math.max(0,Math.round(ms));
    const h=Math.floor(safe/3600000),m=Math.floor((safe%3600000)/60000),s=Math.floor((safe%60000)/1000),fraction=safe%1000;
    return String(h).padStart(2,"0")+":"+String(m).padStart(2,"0")+":"+String(s).padStart(2,"0")+"."+String(fraction).padStart(3,"0");
  }
  // V2 needs a smooth editor playhead. The classic logs panel can contain
  // thousands of React rows, so keep its historical 4 Hz cadence unchanged.
  // State transitions use a forced broadcast below in both interfaces.
  let lastTimeBroadcast = -1;
  function broadcastTime(force=false){
    if(!demo)return;
    const interval=collectTimeline?67:250;
    const bucket=Math.floor(currentTimeMs/interval);
    if(!force&&bucket===lastTimeBroadcast)return;
    lastTimeBroadcast=bucket;
    window.dispatchEvent(new CustomEvent("baroreplay:time",{detail:{
      timeMs:currentTimeMs,durationMs:demo.durationMs,playing:playing,
    }}));
  }
  // Часы по МСК на конкретный момент записи: window.REPLAY_RECORDED_AT_MS —
  // время начала демки (из её собственного заголовка, см. player.tsx/
  // replay_player.html) плюс текущее смещение воспроизведения, переведённое
  // в МСК напрямую (+3ч от UTC) — специально НЕ через toLocaleString с зоной
  // браузера админа: часовой пояс здесь должен быть фиксированным (МСК), а не
  // зависеть от того, где сидит смотрящий запись.
  const MSK_OFFSET_MS = 3*60*60*1000;
  function fmtClockMsk(unixMs){
    const d=new Date(unixMs+MSK_OFFSET_MS);
    const p2=(n)=>String(n).padStart(2,"0");
    return p2(d.getUTCHours())+":"+p2(d.getUTCMinutes())+":"+p2(d.getUTCSeconds())+" МСК";
  }
  function updateTimeUI(forceBroadcast=false){
    if(!demo)return;
    const label=document.getElementById("rpvTimeLabel");if(!label)return; // страница уже ушла со плеера
    label.textContent=fmtTime(currentTimeMs)+" / "+fmtTime(demo.durationMs);
    const rateLabel=document.getElementById("rpvRateLabel");
    if(rateLabel)rateLabel.textContent=(demo.header.tickRate||10)+" Гц";
    const seek=document.getElementById("rpvSeek");
    if(seek){
      if(collectTimeline){
        const bounds=seekWindow||{startMs:0,endMs:demo.durationMs};
        seek.min=String(Math.round(bounds.startMs));
        seek.max=String(Math.round(bounds.endMs));
        seek.step="1";
        seek.setAttribute("aria-valuetext",fmtExactTime(currentTimeMs)+" из "+fmtExactTime(demo.durationMs));
        if(!seek.__dragging)seek.value=String(Math.round(currentTimeMs));
      }else if(!seek.__dragging){
        seek.value=String(Math.round(currentTimeMs/demo.durationMs*1000)||0);
      }
    }
    const clock=document.getElementById("rpvClockLabel");
    if(clock){
      clock.textContent=(typeof window.REPLAY_RECORDED_AT_MS==="number")
        ? fmtClockMsk(window.REPLAY_RECORDED_AT_MS+currentTimeMs)
        : "";
    }
    broadcastTime(forceBroadcast);
  }
  function resyncVoiceAfterSeek(){
    if(!demo||!demo.voice||!demo.voice.count)return;
    resetDecoders();
    voicePtr=voiceIndexAtTime(currentTimeMs);
    while(voicePtr<demo.voice.count&&demo.voice.t[voicePtr]<currentTimeMs)voicePtr++;
    speakingNow.clear();
    speakingRadio.clear();
    updateSpeakingIndicators();
  }
  function applySeek(ms,syncVoice=true){
    if(!demo)return;
    currentTimeMs=Math.max(0,Math.min(demo.durationMs,Number(ms)||0));
    if(syncVoice)resyncVoiceAfterSeek();
    lastChatLen=-1;
    updateChat();
    updateTimeUI(true);
  }
  function applySeekPreview(ms){
    if(!demo)return;
    currentTimeMs=Math.max(0,Math.min(demo.durationMs,Number(ms)||0));
    // Scrubbing can produce hundreds of pointer events per second. Keep this
    // path intentionally cheap: the final commit performs chat and voice
    // resynchronisation once, while UI events retain their normal cadence.
    updateTimeUI(false);
  }
  function setSeekWindow(startMs,endMs){
    if(!collectTimeline||!demo)return;
    const start=Math.max(0,Math.min(demo.durationMs,Math.round(startMs)));
    const end=Math.max(start+1,Math.min(demo.durationMs,Math.round(endMs)));
    seekWindow={startMs:start,endMs:end};
    if(currentTimeMs<start||currentTimeMs>end)applySeek(Math.max(start,Math.min(end,currentTimeMs)));
    else updateTimeUI(true);
  }
  function clearSeekWindow(){
    seekWindow=null;
    updateTimeUI(true);
  }
  function setPlaying(p){
    playing=p;
    const btn=document.getElementById("rpvPlay");
    if(btn){btn.dataset.playing=String(p);btn.setAttribute("aria-label",p?"Пауза":"Воспроизвести");}
    if(p&&audioCtx&&audioCtx.state==="suspended")audioCtx.resume();
    broadcastTime(true);
  }

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
    return demo.voice.store.slice(demo.voice.off[i], demo.voice.len[i]);
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
      if(Math.abs(V.t[k]-currentTimeMs)<=150){
        speakingNow.add(V.sid[k]);
        if(V.radio[k])speakingRadio.add(V.sid[k]);
      }
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
      row.classList.toggle("speaking-radio",speakingNow.has(sid)&&speakingRadio.has(sid));
      row.classList.toggle("speaking-local",speakingNow.has(sid)&&!speakingRadio.has(sid));
    });
  }

  // ── Loop ─────────────────────────────────────────────────────────────────
  function loop(ts){
    if(destroyed)return; // страница ушла со плеера — не трогаем DOM и не планируем следующий кадр
    const dt=lastFrame?(ts-lastFrame):0;lastFrame=ts;
    const prev=currentTimeMs;
    if(playing&&demo){currentTimeMs+=dt*speed;if(currentTimeMs>=demo.durationMs){currentTimeMs=demo.durationMs;setPlaying(false);}}
    if(followSub&&demo&&followSubId!=null){const c=subCenterAt(snapAt(currentTimeMs),followSubId);cam.x=c.x;cam.y=c.y;}
    render();updateChat();updateInventory();updateRadioPanel();updateTimeUI();
    if(voiceEnabled){updateVoice(prev);updateSpeakingIndicators();}
    requestAnimationFrame(loop);
  }

  // ── Players panel (with per-speaker mute + volume) ────────────────────────
  let selectedPlayerName=null;
  function applyPlayerSelection(name,notify=false){
    selectedPlayerName=name||null;
    document.querySelectorAll(".rpv-player").forEach(row=>{
      const rowName=row.dataset.playerName||"";
      const selected=!!selectedPlayerName&&rowName===selectedPlayerName;
      const dimmed=!!selectedPlayerName&&!selected;
      row.dataset.filterSelected=String(selected);
      row.dataset.filterDimmed=String(dimmed);
      row.setAttribute("aria-pressed",String(selected));
    });
    if(notify){
      window.dispatchEvent(new CustomEvent("baroreplay:player-picked",{detail:{name:selectedPlayerName}}));
    }
  }

  function buildPlayersPanel(){
    const box=document.getElementById("rpvPlayers");
    if(!box)return;

    // В панели только те, кто за раунд действительно подавал голос: есть
    // голосовые пакеты либо сообщения в чате. Раньше сюда добавлялись все
    // заспавненные сущности подряд, из-за чего список забивали NPC и монстры,
    // никогда ничего не говорившие, — регулятор громкости и mute для них
    // бессмысленны, а найти среди них живого игрока было невозможно.
    const ids=new Set();
    if(demo.voice&&demo.voice.count){
      for(let i=0;i<demo.voice.count;i++)ids.add(demo.voice.sid[i]);
    }
    for(const c of demo.chat)if(c.senderId)ids.add(c.senderId);
    // Подстраховка на «немой» записи (нет ни голоса, ни чата): показываем
    // хотя бы людей, чтобы панель не оказалась пустой совсем.
    if(ids.size===0){
      for(const sp of demo.spawns.values())if(isHumanSpecies(sp.species))ids.add(sp.id);
    }

    if(ids.size===0){
      box.innerHTML='<div style="color:var(--gray);font-size:12px;padding:8px;">Нет данных об игроках</div>';
      return;
    }

    const nameOf=(id)=>{const sp=demo.spawns.get(id);return(sp&&sp.name)?sp.name:(sp&&sp.species?sp.species:("Игрок #"+id));};
    const ordered=[...ids].sort((a,b)=>nameOf(a).localeCompare(nameOf(b),"ru"));

    speakerState.clear();
    box.innerHTML="";
    for(const id of ordered){
      const sp=demo.spawns.get(id);
      const name=nameOf(id);
      const color=sp?teamColor(sp):C.team0;
      const st={mute:false,vol:1};speakerState.set(id,st);
      const row=document.createElement("div");
      row.className="rpv-player";row.dataset.sid=id;
      if(collectTimeline){
        row.dataset.playerName=name;
        row.setAttribute("role","button");row.setAttribute("tabindex","0");
        row.setAttribute("aria-label","Выделить события игрока "+name);
      }
      row.innerHTML=`<span class="rpv-pdot" style="background:${color}"></span>`+
        `<span class="rpv-pname" title="${esc(name)}">${esc(name)}</span>`+
        `<input type="range" class="rpv-pvol" min="0" max="100" value="100" title="Громкость">`+
        `<button class="rpv-pmute" title="Заглушить">${speakerSoundIcon(false)}</button>`;
      const vol=row.querySelector(".rpv-pvol"),mute=row.querySelector(".rpv-pmute");
      vol.addEventListener("input",()=>{st.vol=vol.value/100;applyGain(id);});
      mute.addEventListener("click",()=>{st.mute=!st.mute;mute.innerHTML=speakerSoundIcon(st.mute);applyGain(id);syncMuteAllBtn();});
      if(collectTimeline){
        const pick=()=>applyPlayerSelection(selectedPlayerName===name?null:name,true);
        row.addEventListener("click",(event)=>{if(event.target.closest("input,button"))return;pick();});
        row.addEventListener("keydown",(event)=>{if(event.target!==row)return;if(event.key==="Enter"||event.key===" "){event.preventDefault();pick();}});
      }
      box.appendChild(row);
    }
    if(collectTimeline)applyPlayerSelection(selectedPlayerName,false);
    syncMuteAllBtn();
  }

  // Кнопка работает как переключатель: пока заглушены не все — глушит всех,
  // когда заглушены все — возвращает звук. Ярлык пересчитывается и после
  // одиночных mute, иначе он врал бы о текущем состоянии панели.
  function speakerSoundIcon(muted){
    if(!collectTimeline)return muted?"🔇":"🔊";
    return muted
      ? '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><line x1="22" y1="9" x2="16" y2="15"></line><line x1="16" y1="9" x2="22" y2="15"></line></svg>'
      : '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path><path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path></svg>';
  }
  function setAllMuted(m){
    for(const[id,st]of speakerState){st.mute=m;applyGain(id);}
    document.querySelectorAll(".rpv-player .rpv-pmute").forEach(b=>{b.innerHTML=speakerSoundIcon(m);});
    syncMuteAllBtn();
  }
  function syncMuteAllBtn(){
    const btn=document.getElementById("rpvMuteAll");
    if(!btn)return;
    const all=speakerState.size>0&&[...speakerState.values()].every(st=>st.mute);
    btn.dataset.mutedAll=String(all);
    const label=btn.querySelector("[data-mute-label]");
    if(label)label.textContent=all?"Вернуть звук":"Заглушить всех";
    btn.classList.toggle("active",all);
    btn.disabled=speakerState.size===0;
  }

  // ── UI wiring ────────────────────────────────────────────────────────────
  function wireUI(){
    onGlobal(document.getElementById("rpvPlay"),"click",()=>setPlaying(!playing));
    document.querySelectorAll(".rpv-speed").forEach(btn=>{
      onGlobal(btn,"click",()=>{speed=parseFloat(btn.dataset.speed);document.querySelectorAll(".rpv-speed").forEach(b=>b.classList.remove("active"));btn.classList.add("active");});
    });
    const seek=document.getElementById("rpvSeek");
    if(seek){
      onGlobal(seek,"input",()=>{
        seek.__dragging=true;
        if(!demo)return;
        const requested=collectTimeline?Number(seek.value):Number(seek.value)/1000*demo.durationMs;
        applySeekPreview(requested);
      });
      const finishSeek=()=>{
        if(!seek.__dragging)return;
        seek.__dragging=false;
        applySeek(currentTimeMs,true);
      };
      onGlobal(seek,"change",finishSeek);
      onGlobal(seek,"pointerup",finishSeek);
      onGlobal(seek,"pointercancel",finishSeek);
      onGlobal(seek,"lostpointercapture",finishSeek);
    }
    onGlobal(document.getElementById("rpvFollow"),"click",(e)=>{
      followSub=!followSub;
      // Цель фиксируется в момент включения — по тому, что сейчас в центре кадра.
      if(followSub)followSubId=pickFollowTarget();
      e.target.classList.toggle("active",followSub);
    });
    // Camera
    onGlobal(canvas,"wheel",(e)=>{e.preventDefault();cam.zoom=Math.max(0.005,Math.min(2,cam.zoom*(e.deltaY<0?1.1:0.9)));},{passive:false});
    let dragging=false,lastX=0,lastY=0;
    onGlobal(canvas,"mousedown",(e)=>{dragging=true;lastX=e.clientX;lastY=e.clientY;});
    onGlobal(window,"mouseup",()=>{dragging=false;});
    onGlobal(window,"mousemove",(e)=>{if(!dragging)return;cam.x-=(e.clientX-lastX)/cam.zoom;cam.y+=(e.clientY-lastY)/cam.zoom;lastX=e.clientX;lastY=e.clientY;followSub=false;document.getElementById("rpvFollow").classList.remove("active");});

    // Voice controls
    const ve=document.getElementById("rpvVoiceEnable");
    if(ve)onGlobal(ve,"change",async(e)=>{
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
    const ma=document.getElementById("rpvMuteAll");
    if(ma)onGlobal(ma,"click",()=>{
      const all=speakerState.size>0&&[...speakerState.values()].every(st=>st.mute);
      setAllMuted(!all);
    });
    const mv=document.getElementById("rpvMasterVol");
    if(mv)onGlobal(mv,"input",(e)=>{masterVol=e.target.value/100;});
    const sp=document.getElementById("rpvSpatial");
    if(sp)onGlobal(sp,"change",(e)=>{spatial=e.target.checked;});
    const fo=document.getElementById("rpvFalloff");
    if(fo)onGlobal(fo,"input",(e)=>{falloffDist=parseInt(e.target.value);});
  }

  function timelineBridgeData(){
    if(!demo)return{replayUrl:window.REPLAY_URL||null,durationMs:0,tickRate:0,players:[],timelineEvents:[]};
    // Entity ids may be reused after a despawn. Resolve the actor at the
    // event's own timestamp instead of consulting only the final spawn map,
    // otherwise an early action can be attributed to a later character.
    const lifecycleById=new Map();
    for(const event of demo.spawnEvents){
      if(!lifecycleById.has(event.id))lifecycleById.set(event.id,[]);
      lifecycleById.get(event.id).push(event);
    }
    const spawnAt=(id,timeMs)=>{
      const lifecycle=lifecycleById.get(id)||[];
      let active=null;
      for(const event of lifecycle){
        if(event.timeMs>timeMs)break;
        active=event.kind==="spawn"?event:null;
      }
      return lifecycle.length?active:(demo.spawns.get(id)||null);
    };
    const nameOf=(id,timeMs)=>{const sp=spawnAt(id,timeMs);return(sp&&sp.name)?sp.name:(sp&&sp.species?sp.species:null);};
    const isHumanSpawn=(spawn)=>isHumanSpecies(spawn?.species);
    const lowerBoundEventTime=(events,timeMs)=>{
      let lo=0,hi=events.length;
      while(lo<hi){const mid=(lo+hi)>>1;if(events[mid].timeMs<timeMs)lo=mid+1;else hi=mid;}
      return lo;
    };
    // v4 SPAWN stores only id/species/name/team. Some recorders also emit a
    // separate meaningful EVENT that directly targets the new entity; index
    // those references once, instead of scanning 300k+ telemetry frames for
    // every spawn. Damage/shot telemetry is deliberately excluded: being hit
    // immediately after appearing does not explain who created the entity.
    const spawnEvidenceByTarget=new Map();
    for(const event of demo.events){
      if(event.kind!==3&&event.kind!==4&&event.kind!==99)continue;
      if(!spawnEvidenceByTarget.has(event.targetId))spawnEvidenceByTarget.set(event.targetId,[]);
      spawnEvidenceByTarget.get(event.targetId).push(event);
    }
    const directSpawnEvidence=(spawn)=>{
      const candidates=spawnEvidenceByTarget.get(spawn.id)||[];
      const from=spawn.timeMs-2_000,to=spawn.timeMs+2_000;
      let index=lowerBoundEventTime(candidates,from),best=null,bestDistance=Infinity;
      for(;index<candidates.length&&candidates[index].timeMs<=to;index++){
        const candidate=candidates[index],distance=Math.abs(candidate.timeMs-spawn.timeMs);
        if(distance<bestDistance){best=candidate;bestDistance=distance;}
      }
      if(!best)return null;
      const actor=nameOf(best.subjectId,best.timeMs);
      return{
        actor:actor||null,
        action:best.kind===3?"used":best.kind===4?"picked_up":"demo_event",
        details:best.text||null,
        offsetMs:best.timeMs,
      };
    };
    const firstSpawnObservation=(spawn)=>{
      if(!demo.snaps?.count)return null;
      let index=API.indexAtTime(demo.snaps,spawn.timeMs);
      if(demo.snaps.t[index]<spawn.timeMs)index++;
      const latest=spawn.timeMs+2_000;
      for(;index<demo.snaps.count&&demo.snaps.t[index]<=latest;index++){
        const snapshot=API.decodeSnapshot(demo.snaps,index);
        const entity=snapshot?.ents.find(item=>item.id===spawn.id);
        if(entity)return{x:entity.x,y:entity.y,observedAtMs:snapshot.timeMs};
      }
      return null;
    };
    const players=[];
    const seenNames=new Set();
    for(const sp of demo.spawnEvents){
      if(sp.kind!=="spawn")continue;
      if(!isHumanSpawn(sp)||!sp.name||seenNames.has(sp.name))continue;
      seenNames.add(sp.name);players.push({id:sp.id,name:sp.name,species:sp.species,teamId:sp.teamId});
    }
    for(const chat of demo.chat){
      if(!chat.author||seenNames.has(chat.author))continue;
      seenNames.add(chat.author);players.push({id:chat.senderId||0,name:chat.author,species:"human",teamId:0});
    }
    players.sort((a,b)=>a.name.localeCompare(b.name,"ru"));

    const timelineEvents=[];
    // Real v4 recordings emit kind=10 for every active affliction tick. Keep
    // those frames for the canvas effect, but index them by victim only as
    // evidence for sampled, real health decreases below.
    const pvpTelemetryByVictim=new Map();
    demo.chat.forEach((chat,index)=>timelineEvents.push({
      id:"chat:"+index,offset_ms:chat.timeMs,player_name:chat.author||nameOf(chat.senderId,chat.timeMs),
      action_type:"chat",details:chat.text||"",source:"demo_chat",lane:"communication",item:null,
    }));
    demo.events.forEach((event,index)=>{
      const subject=nameOf(event.subjectId,event.timeMs),target=nameOf(event.targetId,event.timeMs);
      let action=null,playerName=subject,details=event.text||"",lane="system";
      // In the file format subjectId is the damaged entity and targetId is
      // the source. A previous implementation treated them in reverse and
      // grouped every recurring status tick as a new hit.
      if(event.kind===10){
        const victimSpawn=spawnAt(event.subjectId,event.timeMs),attackerSpawn=spawnAt(event.targetId,event.timeMs);
        const amount=Number.isFinite(event.amount)?Math.max(0,event.amount):0;
        if(event.subjectId!==event.targetId&&amount>0&&isHumanSpawn(victimSpawn)&&isHumanSpawn(attackerSpawn)&&victimSpawn.name&&attackerSpawn.name){
          if(!pvpTelemetryByVictim.has(event.subjectId))pvpTelemetryByVictim.set(event.subjectId,[]);
          pvpTelemetryByVictim.get(event.subjectId).push(event);
        }
        return;
      }
      if(event.kind===1){action="death";playerName=target||subject;lane="combat";if(!details)details=subject?"Погиб · источник: "+subject:"Погиб";}
      else if(event.kind===2){action="attack";lane="combat";if(!details)details=target?"Убийство · цель: "+target:"Убийство";}
      else if(event.kind===3){
        action="used";lane="inventory";
        if(!details)details=target&&target!==subject?"Использовал предмет · цель: "+target:"Использовал предмет";
      }
      else if(event.kind===4){
        action="picked_up";lane="inventory";
        if(!details)details=target&&target!==subject?"Поднял предмет · объект: "+target:"Поднял предмет";
      }
      else if(event.kind===99&&details.trim()){action="demo_event";}
      else if(event.kind!==10&&event.kind!==11&&details.trim()){action="demo_event";}
      if(!action)return;
      timelineEvents.push({
        id:"demo:"+index,offset_ms:event.timeMs,player_name:playerName,action_type:action,
        details,source:"demo_event",lane,item:null,
      });
    });
    const pvpDamage=[];
    const lastDamageByPair=new Map();
    for(const drop of demo.healthDrops||[]){
      const telemetry=pvpTelemetryByVictim.get(drop.victimId)||[];
      const from=drop.timeMs-200,to=drop.timeMs+100;
      let index=lowerBoundEventTime(telemetry,from);
      const sources=new Map();
      for(;index<telemetry.length&&telemetry[index].timeMs<=to;index++){
        const raw=telemetry[index];
        const attacker=spawnAt(raw.targetId,raw.timeMs);
        if(!isHumanSpawn(attacker)||!attacker.name||attacker.name===drop.victimName)continue;
        sources.set(attacker.name,attacker);
      }
      // Ambiguous simultaneous sources are deliberately omitted. Guessing an
      // attacker in an administrative replay is worse than showing no row.
      if(sources.size!==1)continue;
      const attacker=sources.keys().next().value;
      const pair=attacker+"\u0000"+drop.victimName;
      const previous=lastDamageByPair.get(pair);
      if(previous&&drop.timeMs-previous.lastTimeMs<=750){
        previous.healthLossPct+=drop.healthLossPct;
        previous.lastTimeMs=drop.timeMs;
      }else{
        const damage={timeMs:drop.timeMs,lastTimeMs:drop.timeMs,attacker,target:drop.victimName,healthLossPct:drop.healthLossPct};
        pvpDamage.push(damage);lastDamageByPair.set(pair,damage);
      }
    }
    pvpDamage.forEach((event,pvpIndex)=>{
      const rounded=Math.round(event.healthLossPct*10)/10;
      timelineEvents.push({
        id:"demo-pvp:"+pvpIndex,offset_ms:event.timeMs,player_name:event.attacker,
        action_type:"damage",details:"Нанёс урон · около "+rounded.toLocaleString("ru-RU")+"% здоровья · цель: "+event.target,
        source:"demo_pvp",lane:"combat",item:null,target_name:event.target,damage_pct:rounded,
      });
    });
    demo.spawnEvents.forEach((event,index)=>{
      // Keep the complete entity lifecycle. Creature, pet, NPC and human
      // appearances are investigation evidence just like player actions; the
      // editor clusters dense markers visually instead of discarding facts.
      const entityName=event.name||event.species||"Неизвестная сущность";
      const entityKind=isHumanSpecies(event.species)?"Персонаж":"Сущность";
      const speciesDetails=event.species?" · вид: "+event.species:"";
      const evidence=event.kind==="spawn"?directSpawnEvidence(event):null;
      const observation=event.kind==="spawn"?firstSpawnObservation(event):null;
      const evidenceDetails=evidence
        ? " · связанное действие: "+(evidence.actor?evidence.actor+" · ":"")+(evidence.details||evidence.action)
        : event.kind==="spawn"?" · причина и инициатор не записаны":"";
      timelineEvents.push({
        id:"spawn:"+index,offset_ms:event.timeMs,player_name:entityName,
        action_type:event.kind,
        details:(event.kind==="spawn"?entityKind+" появился":entityKind+" исчез")+speciesDetails+evidenceDetails,
        source:"demo_spawn",lane:"system",item:null,
        entity_species:event.species||null,entity_team_id:event.teamId??null,
        entity_id:event.id,
        entity_world_x:observation?.x??null,entity_world_y:observation?.y??null,
        entity_observed_at_ms:observation?.observedAtMs??null,
        spawn_evidence_kind:evidence?"direct_event":"unavailable",
        spawn_source_name:evidence?.actor??null,
        spawn_source_action:evidence?.action??null,
        spawn_source_details:evidence?.details??null,
      });
    });
    timelineEvents.sort((a,b)=>a.offset_ms-b.offset_ms||a.id.localeCompare(b.id));
    return{replayUrl:window.REPLAY_URL||null,durationMs:demo.durationMs,tickRate:demo.header.tickRate,players,timelineEvents};
  }

  function broadcastReady(){
    if(!demo||!collectTimeline)return;
    window.dispatchEvent(new CustomEvent("baroreplay:ready",{detail:timelineBridgeData()}));
  }

  // Мост для внешнего интерфейса (панель логов и монтажная шкала React):
  // перемотка, чтение времени, выбор игрока и безопасный индекс событий.
  // Вся тяжёлая отрисовка и разбор бинарного демо остаются здесь.
  //
  // destroy() вызывается из cleanup-функции React-эффекта (player.tsx) ПЕРЕД
  // тем, как удалить <script>-теги этого инстанса и/или смонтировать новый —
  // без него старый requestAnimationFrame-цикл продолжал бы жить и падать,
  // как только пользователь уходит со страницы плеера совсем (см. loop()).
  const replayBridge = {
    instanceId: replayInstanceId,
    seekTo(ms){
      applySeek(ms,true);
    },
    previewSeek(ms){
      applySeekPreview(ms);
    },
    commitSeek(){
      applySeek(currentTimeMs,true);
    },
    stepBy(ms){
      applySeek(currentTimeMs+ms,true);
    },
    getTimeMs(){return currentTimeMs;},
    getDurationMs(){return demo?demo.durationMs:0;},
    isPlaying(){return playing;},
    setSeekWindow(startMs,endMs){setSeekWindow(startMs,endMs);},
    clearSeekWindow(){clearSeekWindow();},
    getTimelineData(){return timelineBridgeData();},
    setPlaying(p){setPlaying(!!p);},
    selectPlayer(name){applyPlayerSelection(name||null,false);},
    getSelectedPlayer(){return selectedPlayerName;},
    destroy(){
      if(destroyed)return;
      destroyed=true;
      playing=false;
      loadController.abort();
      resetDecoders();
      if(audioCtx){try{audioCtx.close();}catch(e){/*ignore*/}audioCtx=null;}
      for(const [target,type,handler,opts] of globalListeners){
        try{target.removeEventListener(type,handler,opts);}catch(e){/*ignore*/}
      }
      globalListeners.length=0;
      if(window.__BaroReplayUI===replayBridge)delete window.__BaroReplayUI;
    },
  };
  window.__BaroReplayUI = replayBridge;

  // ── Загрузка: этапы и прогресс ──────────────────────────────────────────
  // Каждый этап отмечается в списке #rpvLoadingStages (если он есть в
  // разметке — старый статический шаблон его не содержит, поэтому все
  // обращения защищены) классами done/active, а полоса прогресса заполняется
  // только там, где реально известен процент (скачивание по Content-Length);
  // для распаковки/разбора точного процента нет — полоса просто держит своё
  // текущее положение, а крутится спиннер.
  // Скачивание и распаковка теперь идут ОДНОВРЕМЕННО (каждый пришедший из
  // сети кусок сразу уходит в потоковый распаковщик — см. loadDemoBytes),
  // поэтому событие прогресса скачивания продолжает приходить и ПОСЛЕ того,
  // как этап "unpack" уже начался. Список этапов не должен из-за этого
  // откатываться визуально назад — двигаем маркер только вперёд; текст
  // детали (проценты) при этом обновлять можно всегда, это просто честная
  // информация о том, что скачивание фоном ещё продолжается.
  const STAGE_KEYS = ["download","unpack","parse","scene"];
  let maxStageIdx = -1;
  function setStage(stage, detail){
    if(destroyed||!loadingText)return;
    const idx = STAGE_KEYS.indexOf(stage);
    if(idx < maxStageIdx){ if(detail)loadingText.textContent=detail; return; }
    maxStageIdx = idx;
    if(loadingStages){
      for(const li of loadingStages.children){
        const liIdx = STAGE_KEYS.indexOf(li.dataset.stage);
        if(liIdx===idx){li.classList.add("active");li.classList.remove("done");}
        else if(liIdx<idx){li.classList.add("done");li.classList.remove("active");}
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
      const store=await API.loadDemoBytes(window.REPLAY_URL, onLoadProgress, loadController.signal);
      if(destroyed)return; // ушли со страницы, пока файл качался/распаковывался

      setStage("parse", STAGE_LABEL.parse+"…");
      setProgressBar(null);
      // Разбор — синхронная функция без промежуточных await, отдать кадр
      // браузеру для отрисовки текста этапа перед тяжёлой работой:
      await new Promise(requestAnimationFrame);
      if(destroyed)return;
      demo=API.parseDemo(store);
      // NOTE: store (PagedBuffer) остаётся жить в demo.voice.store/demo.snaps.store
      // для ленивой распаковки voice-пакетов и снапшотов — так дешевле, чем
      // заранее декодировать тысячи мелких пакетов в объекты JS.

      setStage("scene", STAGE_LABEL.scene+"…");
      autoFit();buildPlayersPanel();
      if(destroyed)return;

      loadingEl.style.display="none";
      broadcastReady();
      updateTimeUI();
      requestAnimationFrame(loop);
    }catch(e){
      if(destroyed||e?.name==="AbortError")return; // страница уже ушла — некому показывать ошибку
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
    onGlobal(document,"DOMContentLoaded",boot);
  } else {
    boot();
  }
})();
})();
