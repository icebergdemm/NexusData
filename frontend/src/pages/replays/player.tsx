import { useEffect, useState } from "react"
import { useParams, Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { ReplayLogsPanel } from "./ReplayLogsPanel"
import { ReplayTimelineWorkbench } from "./ReplayTimelineWorkbench"
import { ReplayEntityInventory } from "./ReplayEntityInventory"
import { Camera, Clapperboard, Download, MessageSquare, Pause, Play, Users, Volume2, VolumeX } from "lucide-react"
import { AdaptiveIcon } from "@/components/layout/PlatformIcon"
import { IS_V2 } from "@/lib/ui-version"
import { V2ReplayTransport } from "./V2ReplayTransport"

let replayScriptGeneration = 0

interface ReplayDetail {
  replay: {
    id: number; submarine_name: string | null; level_name: string | null
    recorded_at: string | null; has_voice: boolean; duration_ms: number | null
    filename: string | null; file_size: number | null
    replay_kind: "round" | "lobby"
  }
}

/**
 * Wraps the legacy vanilla-JS canvas replay renderer (static/js/replay_player.js)
 * as-is rather than rewriting it — it's a large, self-contained binary-format
 * parser/renderer with no React-idiomatic equivalent worth building from scratch.
 * Markup/CSS/element IDs are kept identical to templates/admin/replay_player.html
 * so the script's DOM queries resolve unchanged.
 */
export function ReplayPlayerPage() {
  const { replayId } = useParams<{ replayId: string }>()
  const [scriptsReady, setScriptsReady] = useState(false)

  const { data } = useQuery({
    queryKey: ["replay-detail", replayId],
    queryFn: () => api.get<ReplayDetail>(`/api/v1/admin/replays/${replayId}`),
  })
  const isLobby = data?.replay.replay_kind === "lobby"

  useEffect(() => {
    // Wait until the replay metadata (has_voice) is known so the scripts are
    // only ever injected once per replay, with the correct globals already set.
    if (!replayId || !data) return
    setScriptsReady(false)
    const runtime = window as unknown as {
      REPLAY_URL: string
      HAS_VOICE: boolean
      REPLAY_RECORDED_AT_MS: number | null
      __BARO_REPLAY_ACTIVE_INSTANCE__?: string
      __BaroReplayUI?: { destroy?: () => void }
    }
    const instanceId = `replay-${replayId}-${++replayScriptGeneration}`
    runtime.__BARO_REPLAY_ACTIVE_INSTANCE__ = instanceId
    runtime.REPLAY_URL = `/api/v1/admin/replays/${replayId}/download`
    runtime.HAS_VOICE = data.replay.has_voice
    // recorded_at приходит без указания зоны (naive UTC, см. services_replays.py) —
    // без явного "Z" Date.parse трактовал бы строку как локальное время браузера
    // администратора, а не как UTC. Плеер сам переводит это в МСК (см. rpvClockLabel).
    const recordedAtIso = data.replay.recorded_at
    runtime.REPLAY_RECORDED_AT_MS =
      recordedAtIso ? Date.parse(recordedAtIso.endsWith("Z") ? recordedAtIso : recordedAtIso + "Z") : null

    const scripts: HTMLScriptElement[] = []
    let cancelled = false

    function loadScript(src: string, guardedInstanceId?: string): Promise<void> {
      return new Promise((resolve, reject) => {
        const s = document.createElement("script")
        s.src = src
        if (guardedInstanceId) s.dataset.replayInstanceId = guardedInstanceId
        s.onload = () => resolve()
        s.onerror = () => reject(new Error(`failed to load ${src}`))
        document.body.appendChild(s)
        scripts.push(s)
      })
    }

    // ?v=<отпечаток сборки> — обязателен. Файл подключается напрямую тегом
    // <script src>, минуя бандлер, поэтому контентного хэша в имени у него
    // нет, и для браузера URL всегда один и тот же. Кэш /static раньше
    // держал копию сутками (Cache-Control: max-age=86400), из-за чего
    // выкаченные на сервер правки этого скрипта по несколько раз подряд
    // просто не доезжали до пользователя — исполнялась старая копия, а
    // ошибки в консоли указывали на строки, которых в новом файле уже нет.
    // Сервер теперь отдаёт static/js/* с no-cache (main.py), но это не
    // отменяет УЖЕ закэшированную копию: только смена URL заставляет
    // браузер сходить за файлом заново.
    loadScript("https://cdn.jsdelivr.net/npm/fflate@0.8.2/umd/index.js")
      .then(() => {
        if (cancelled || runtime.__BARO_REPLAY_ACTIVE_INSTANCE__ !== instanceId) return
        return loadScript(`/static/js/replay_player.js?v=${__BUILD_ID__}`, instanceId)
      })
      .then(() => {
        if (!cancelled && runtime.__BARO_REPLAY_ACTIVE_INSTANCE__ === instanceId) setScriptsReady(true)
      })
      .catch((e) => console.error(e))

    return () => {
      cancelled = true
      // Скрипт держит собственный requestAnimationFrame-цикл и загрузку —
      // удаление <script>-тегов ниже не останавливает уже выполнившийся код,
      // только мешает будущей повторной загрузке. Без явной остановки старый
      // цикл продолжал бы дёргать document.getElementById по узлам, которых
      // здесь больше нет, как только эта страница размонтируется целиком
      // (не просто сменился replayId — тогда #rpvCanvas переиспользуется тем
      // же деревом, а вот уход со страницы плеера убирает его насовсем).
      // Removing an in-flight dynamic <script> does not prevent browsers from
      // executing it after the response arrives. Invalidate this generation
      // first: replay_player.js reads the id from document.currentScript and
      // exits before touching the DOM if this effect is no longer current.
      if (runtime.__BARO_REPLAY_ACTIVE_INSTANCE__ === instanceId) {
        runtime.__BaroReplayUI?.destroy?.()
        delete runtime.__BARO_REPLAY_ACTIVE_INSTANCE__
      }
      for (const s of scripts) s.remove()
    }
    // Re-run whenever the replay id changes (navigating from one replay to another)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replayId, data])

  return (
    <div className={isLobby ? "rpv-lobby" : undefined}>
      <style>{`
        .rpv-top{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:12px;}
        .rpv-top h2{display:flex;align-items:center;gap:6px;font-size:18px;font-weight:700;color:var(--foreground);margin:0;}
        .rpv-top-actions{display:flex;align-items:center;gap:8px;}
        .rpv-download{text-decoration:none;white-space:nowrap;}
        .rpv-back{color:var(--muted-foreground);text-decoration:none;font-size:13px;}
        .rpv-back:hover{color:var(--foreground);}
        .rpv-layout{display:grid;grid-template-columns:1fr 300px;gap:14px;align-items:start;}
        @media(max-width:1000px){.rpv-layout{grid-template-columns:1fr;}}
        .rpv-canvas-wrap{position:relative;background:#0A0E14;border:1px solid var(--border);border-radius:10px;overflow:hidden;}
        #rpvCanvas{display:block;width:100%;height:70vh;cursor:grab;}
        #rpvCanvas:active{cursor:grabbing;}
        .rpv-loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:var(--muted-foreground);font-size:14px;background:#0A0E14;z-index:5;}
        .rpv-spinner{width:36px;height:36px;border:3px solid var(--border);border-top-color:var(--primary);border-radius:50%;animation:rpvspin 0.8s linear infinite;}
        .rpv-loading-bar{width:220px;height:5px;border-radius:3px;background:var(--border);overflow:hidden;}
        .rpv-loading-bar-fill{height:100%;width:0%;background:var(--primary);border-radius:3px;transition:width 0.2s ease;}
        .rpv-loading-bar-fill.indeterminate{width:40%!important;animation:rpvbarslide 1.1s ease-in-out infinite;}
        @keyframes rpvbarslide{0%{transform:translateX(-100%);}100%{transform:translateX(350%);}}
        .rpv-loading-stages{list-style:none;display:flex;flex-direction:column;gap:5px;margin:0;padding:0;font-size:12px;}
        .rpv-loading-stages li{color:var(--muted-foreground);opacity:.45;display:flex;align-items:center;gap:6px;transition:opacity 0.2s,color 0.2s;}
        .rpv-loading-stages li::before{content:"○";font-size:10px;}
        .rpv-loading-stages li.done{opacity:.75;color:var(--success,#22d06f);}
        .rpv-loading-stages li.done::before{content:"●";}
        .rpv-loading-stages li.active{opacity:1;color:var(--foreground);font-weight:600;}
        .rpv-loading-stages li.active::before{content:"◐";animation:rpvspin 0.9s linear infinite;display:inline-block;}
        @keyframes rpvspin{to{transform:rotate(360deg);}}
        .rpv-controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;background:var(--card);border:1px solid var(--border);border-radius:10px;margin-top:10px;}
        .rpv-btn{display:inline-flex;align-items:center;gap:6px;background:var(--background);border:1px solid var(--border);color:var(--foreground);border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer;font-weight:600;}
        #rpvPlay [data-pause]{display:none;}#rpvPlay[data-playing="true"] [data-play]{display:none;}#rpvPlay[data-playing="true"] [data-pause]{display:block;}
        #rpvMuteAll [data-unmute]{display:none;}#rpvMuteAll[data-muted-all="true"] [data-mute]{display:none;}#rpvMuteAll[data-muted-all="true"] [data-unmute]{display:block;}
        .rpv-btn:hover{border-color:var(--primary);}
        .rpv-btn.active{background:var(--primary);border-color:var(--primary);}
        .rpv-seek{flex:1;min-width:160px;}
        .rpv-time{font-family:monospace;font-size:12px;color:var(--muted-foreground);white-space:nowrap;}
        .rpv-side{display:flex;flex-direction:column;gap:12px;max-height:calc(70vh + 62px);overflow-y:auto;}
        .rpv-panel{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px;}
        .rpv-panel h3{font-size:13px;font-weight:700;color:var(--foreground);margin:0 0 10px;}
        .rpv-panel-head{display:flex;align-items:center;justify-content:space-between;gap:8px;}
        .rpv-mute-all{padding:3px 8px;font-size:11px;font-weight:600;}
        .rpv-mute-all:disabled{opacity:.4;cursor:default;}
        .rpv-row{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;font-size:12px;color:var(--muted-foreground);}
        .rpv-row label{display:flex;align-items:center;gap:6px;cursor:pointer;}
        .rpv-slider{width:110px;}
        .rpv-players{display:flex;flex-direction:column;gap:4px;max-height:240px;overflow-y:auto;}
        .rpv-player{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;background:var(--background);font-size:12px;}
        .rpv-player.speaking{box-shadow:0 0 0 2px var(--success) inset;}
        .rpv-pdot{width:10px;height:10px;border-radius:50%;flex-shrink:0;}
        .rpv-pname{flex:1;color:var(--foreground);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
        .rpv-pmute{background:none;border:none;cursor:pointer;font-size:13px;opacity:.6;}
        .rpv-pmute:hover{opacity:1;}
        .rpv-pvol{width:60px;}
        .rpv-chat{max-height:200px;overflow-y:auto;display:flex;flex-direction:column;gap:3px;font-size:12px;}
        .rpv-chat-line{color:var(--muted-foreground);}
        .rpv-chat-line b{color:var(--info);}
        .rpv-err{color:var(--destructive);padding:20px;font-size:13px;}
        html[data-ui-version="v2"] .rpv-layout{grid-template-columns:minmax(0,1fr) 320px;}
        html[data-ui-version="v2"] #rpvCanvas{height:58vh;min-height:430px;}
        html[data-ui-version="v2"] .rpv-side{max-height:calc(58vh + 62px);}
        html[data-ui-version="v2"] .rpv-canvas-wrap,
        html[data-ui-version="v2"] .rpv-controls,
        html[data-ui-version="v2"] .rpv-panel,
        html[data-ui-version="v2"] .rpv-btn,
        html[data-ui-version="v2"] .rpv-player{border-radius:0;}
        html[data-ui-version="v2"] .rpv-controls{gap:8px;overflow:visible;}
        html[data-ui-version="v2"] .rpv-v2-transport{display:contents;}
        html[data-ui-version="v2"] .rpv-step{min-width:52px;justify-content:center;padding-inline:9px;font-variant-numeric:tabular-nums;}
        html[data-ui-version="v2"] .rpv-seek-shell{--rpv-hold-origin:50%;position:relative;flex:1 1 300px;min-width:220px;display:grid;grid-template-rows:20px 0fr;align-items:center;transition:grid-template-rows 220ms cubic-bezier(.2,.8,.2,1),padding 220ms cubic-bezier(.2,.8,.2,1);}
        html[data-ui-version="v2"] .rpv-seek-shell[data-precision="true"]{grid-template-rows:28px 54px;padding-top:2px;}
        html[data-ui-version="v2"] .rpv-seek-shell .rpv-seek{grid-row:1;width:100%;min-width:0;margin:0;accent-color:var(--foreground);height:18px;cursor:ew-resize;transition:height 220ms cubic-bezier(.2,.8,.2,1);}
        html[data-ui-version="v2"] .rpv-seek-shell[data-precision="true"] .rpv-seek{height:28px;}
        html[data-ui-version="v2"] .rpv-seek-shell .rpv-seek:focus-visible{outline:2px solid var(--ring);outline-offset:3px;}
        html[data-ui-version="v2"] .rpv-hold-progress{position:absolute;z-index:2;left:0;right:0;top:9px;height:2px;pointer-events:none;background:var(--foreground);opacity:0;transform:scaleX(0);transform-origin:var(--rpv-hold-origin) center;}
        html[data-ui-version="v2"] .rpv-seek-shell[data-holding="true"] .rpv-hold-progress{animation:rpv-hold-progress 2.65s linear .35s forwards;}
        @keyframes rpv-hold-progress{0%{opacity:0;transform:scaleX(0)}5%{opacity:.72}100%{opacity:.9;transform:scaleX(1)}}
        html[data-ui-version="v2"] .rpv-precision-panel{grid-row:2;min-height:0;overflow:hidden;display:grid;grid-template-columns:auto minmax(90px,1fr) auto;align-items:center;gap:12px;border-top:1px solid transparent;opacity:0;transform:translateY(-5px);pointer-events:none;visibility:hidden;transition:opacity 180ms ease,transform 220ms cubic-bezier(.2,.8,.2,1),border-color 220ms ease,visibility 0s linear 220ms;}
        html[data-ui-version="v2"] .rpv-seek-shell[data-precision="true"] .rpv-precision-panel{opacity:1;transform:translateY(0);border-color:var(--border);pointer-events:auto;visibility:visible;transition-delay:0s;}
        html[data-ui-version="v2"] .rpv-precision-copy{display:flex;flex-direction:column;gap:2px;white-space:nowrap;font-family:var(--font-mono,monospace);font-size:10px;color:var(--muted-foreground);text-transform:uppercase;letter-spacing:.06em;}
        html[data-ui-version="v2"] .rpv-precision-copy span:first-child{display:flex;align-items:center;gap:5px;color:var(--foreground);font-weight:700;}
        html[data-ui-version="v2"] .rpv-precision-copy svg{width:12px;height:12px;}
        html[data-ui-version="v2"] .rpv-precision-ruler{height:20px;border-inline:1px solid var(--border);background:repeating-linear-gradient(90deg,var(--muted-foreground) 0 1px,transparent 1px 10%),repeating-linear-gradient(90deg,var(--border) 0 1px,transparent 1px 2%);background-size:100% 12px,100% 6px;background-position:center bottom;background-repeat:no-repeat;opacity:.72;}
        html[data-ui-version="v2"] .rpv-precision-close{width:26px;height:26px;display:grid;place-items:center;border:1px solid var(--border);background:var(--background);color:var(--muted-foreground);cursor:pointer;}
        html[data-ui-version="v2"] .rpv-precision-close:hover{color:var(--foreground);border-color:var(--foreground);}
        html[data-ui-version="v2"] .rpv-precision-close svg{width:13px;height:13px;}
        html[data-ui-version="v2"] .rpv-direct-time{position:relative;display:flex;align-items:center;gap:6px;font-family:var(--font-mono,monospace);font-size:11px;color:var(--muted-foreground);white-space:nowrap;}
        html[data-ui-version="v2"] .rpv-direct-time>label{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}
        html[data-ui-version="v2"] .rpv-direct-time input{width:112px;height:30px;border:1px solid var(--border);background:var(--background);color:var(--foreground);padding:0 8px;font:inherit;font-size:12px;font-variant-numeric:tabular-nums;outline:none;}
        html[data-ui-version="v2"] .rpv-direct-time input:focus{border-color:var(--foreground);box-shadow:0 0 0 1px var(--foreground);}
        html[data-ui-version="v2"] .rpv-direct-time input[aria-invalid="true"]{border-color:var(--destructive);}
        html[data-ui-version="v2"] #rpvDirectTimeError{position:absolute;z-index:4;top:calc(100% + 5px);left:0;padding:4px 6px;border:1px solid var(--destructive);background:var(--background);color:var(--destructive);font-size:10px;}
        @media(prefers-reduced-motion:reduce){html[data-ui-version="v2"] .rpv-seek-shell,html[data-ui-version="v2"] .rpv-seek-shell .rpv-seek,html[data-ui-version="v2"] .rpv-precision-panel{transition-duration:1ms;}html[data-ui-version="v2"] .rpv-seek-shell[data-holding="true"] .rpv-hold-progress{animation-timing-function:steps(1,end);}}
        @media(max-width:1000px){html[data-ui-version="v2"] .rpv-layout{grid-template-columns:1fr;}html[data-ui-version="v2"] .rpv-side{max-height:none;}}
        .rpv-lobby .rpv-layout{grid-template-columns:minmax(0,900px);justify-content:center;}
        .rpv-lobby .rpv-canvas-wrap{position:absolute;width:1px;height:1px;clip-path:inset(50%);}
        .rpv-lobby .rpv-side{display:grid;grid-template-columns:minmax(260px,1fr) minmax(320px,2fr);max-height:none;overflow:visible;}
        .rpv-lobby .rpv-controls{margin-top:0;}
        @media(max-width:700px){.rpv-lobby .rpv-side{grid-template-columns:1fr;}}
      `}</style>

      <Link to="/replays" className="rpv-back">← Назад к списку</Link>
      <div className="rpv-top">
        <h2>
          <AdaptiveIcon emoji="🎬" icon={Clapperboard} /> {isLobby ? "Демо лобби" : (data?.replay.submarine_name ?? "Раунд")} #{replayId}
          {!isLobby && data?.replay.level_name && <span style={{ color: "var(--muted-foreground)", fontWeight: 400, fontSize: 14 }}> · {data.replay.level_name}</span>}
        </h2>
        <div className="rpv-top-actions">
          <span className="rpv-time">{data?.replay.recorded_at ? formatBrowserDate(data.replay.recorded_at) : ""}</span>
          {IS_V2 && replayId && (
            <a
              className="rpv-btn rpv-download"
              href={`/api/v1/admin/replays/${replayId}/archive`}
              download={`replay_${replayId}.barodemo.zip`}
              title="Скачать исходную демозапись в ZIP"
            >
              <Download className="size-3.5" aria-hidden="true" /> ZIP
            </a>
          )}
        </div>
      </div>

      <div className="rpv-layout">
        <div>
          <div className="rpv-canvas-wrap">
            <canvas id="rpvCanvas" />
            {!isLobby && replayId && <ReplayEntityInventory replayId={replayId} />}
            <div className="rpv-loading" id="rpvLoading">
              <div className="rpv-spinner" />
              <div id="rpvLoadingText">{scriptsReady ? "Загрузка реплея..." : "Загрузка плеера..."}</div>
              <div className="rpv-loading-bar"><div className="rpv-loading-bar-fill" id="rpvLoadingBarFill" /></div>
              <ul className="rpv-loading-stages" id="rpvLoadingStages">
                <li data-stage="download">Скачивание демо-записи</li>
                <li data-stage="unpack">Распаковка</li>
                <li data-stage="parse">Разбор данных раунда</li>
                <li data-stage="scene">Подготовка сцены</li>
              </ul>
            </div>
          </div>

          <div className="rpv-controls">
            <button className="rpv-btn" id="rpvPlay" aria-label="Воспроизвести"><span data-play><AdaptiveIcon emoji="▶" icon={Play} className="size-3.5" /></span><Pause data-pause className="size-3.5" /></button>
            <span className={IS_V2 ? "sr-only" : "rpv-time"} id="rpvTimeLabel">00:00 / 00:00</span>
            <span className="rpv-time" id="rpvClockLabel" title="Время по МСК в этот момент записи"></span>
            {IS_V2
              ? <V2ReplayTransport key={replayId} initialDurationMs={data?.replay.duration_ms} />
              : <input type="range" className="rpv-seek" id="rpvSeek" min={0} max={1000} defaultValue={0} />}
            <div style={{ display: "flex", gap: 4 }}>
              <button className="rpv-btn rpv-speed" data-speed="0.5">0.5×</button>
              <button className="rpv-btn rpv-speed active" data-speed="1">1×</button>
              <button className="rpv-btn rpv-speed" data-speed="2">2×</button>
              <button className="rpv-btn rpv-speed" data-speed="4">4×</button>
            </div>
            <button className="rpv-btn" id="rpvFollow" style={isLobby ? { display: "none" } : undefined}><AdaptiveIcon emoji="📷" icon={Camera} /> Следить за лодкой</button>
          </div>
        </div>

        <div className="rpv-side">
          <div className="rpv-panel">
            <h3 className="flex items-center gap-1.5"><AdaptiveIcon emoji="🔊" icon={Volume2} />Звук</h3>
            <div className="rpv-row"><label><input type="checkbox" id="rpvVoiceEnable" /> Включить голос</label></div>
            <div className="rpv-row"><span>Общая громкость</span><input type="range" className="rpv-slider" id="rpvMasterVol" min={0} max={100} defaultValue={100} /></div>
            <div className="rpv-row" style={isLobby ? { display: "none" } : undefined}><label><input type="checkbox" id="rpvSpatial" defaultChecked={!isLobby} /> Затухание по расстоянию</label></div>
            <div className="rpv-row" style={isLobby ? { display: "none" } : undefined}><span title="Дистанция, на которой голос затухает до нуля">Радиус слышимости</span><input type="range" className="rpv-slider" id="rpvFalloff" min={500} max={8000} step={100} defaultValue={3000} /></div>
          </div>
          <div className="rpv-panel">
            <h3 className="rpv-panel-head">
              <span className="flex items-center gap-1.5"><AdaptiveIcon emoji="👥" icon={Users} />Игроки</span>
              <button className="rpv-btn rpv-mute-all" id="rpvMuteAll"><span data-mute><AdaptiveIcon emoji="🔇" icon={VolumeX} /></span><Volume2 data-unmute className="size-3.5" /><span data-mute-label>Заглушить всех</span></button>
            </h3>
            <div className="rpv-players" id="rpvPlayers" />
          </div>
          {!isLobby && <div className="rpv-panel">
            <h3 className="flex items-center gap-1.5"><AdaptiveIcon emoji="💬" icon={MessageSquare} />Чат</h3>
            <div className="rpv-chat" id="rpvChat" />
          </div>}
          {!IS_V2 && replayId && !isLobby && <ReplayLogsPanel replayId={replayId} />}
        </div>
      </div>
      {IS_V2 && replayId && !isLobby && (
        <ReplayTimelineWorkbench replayId={replayId} initialDurationMs={data?.replay.duration_ms} />
      )}
    </div>
  )
}
