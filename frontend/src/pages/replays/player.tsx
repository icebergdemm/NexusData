import { useEffect, useState } from "react"
import { useParams, Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { ReplayLogsPanel } from "./ReplayLogsPanel"

interface ReplayDetail {
  replay: {
    id: number; submarine_name: string | null; level_name: string | null
    recorded_at: string | null; has_voice: boolean
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

  useEffect(() => {
    // Wait until the replay metadata (has_voice) is known so the scripts are
    // only ever injected once per replay, with the correct globals already set.
    if (!replayId || !data) return
    ;(window as unknown as { REPLAY_URL: string }).REPLAY_URL = `/api/v1/admin/replays/${replayId}/download`
    ;(window as unknown as { HAS_VOICE: boolean }).HAS_VOICE = data.replay.has_voice

    const scripts: HTMLScriptElement[] = []
    let cancelled = false

    function loadScript(src: string): Promise<void> {
      return new Promise((resolve, reject) => {
        const s = document.createElement("script")
        s.src = src
        s.onload = () => resolve()
        s.onerror = () => reject(new Error(`failed to load ${src}`))
        document.body.appendChild(s)
        scripts.push(s)
      })
    }

    loadScript("https://cdn.jsdelivr.net/npm/fflate@0.8.2/umd/index.js")
      .then(() => loadScript("/static/js/replay_player.js"))
      .then(() => { if (!cancelled) setScriptsReady(true) })
      .catch((e) => console.error(e))

    return () => {
      cancelled = true
      for (const s of scripts) s.remove()
    }
    // Re-run whenever the replay id changes (navigating from one replay to another)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replayId, data])

  return (
    <div>
      <style>{`
        .rpv-top{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:12px;}
        .rpv-top h2{font-size:18px;font-weight:700;color:var(--foreground);margin:0;}
        .rpv-back{color:var(--muted-foreground);text-decoration:none;font-size:13px;}
        .rpv-back:hover{color:var(--foreground);}
        .rpv-layout{display:grid;grid-template-columns:1fr 300px;gap:14px;align-items:start;}
        @media(max-width:1000px){.rpv-layout{grid-template-columns:1fr;}}
        .rpv-canvas-wrap{position:relative;background:#0A0E14;border:1px solid var(--border);border-radius:10px;overflow:hidden;}
        #rpvCanvas{display:block;width:100%;height:70vh;cursor:grab;}
        #rpvCanvas:active{cursor:grabbing;}
        .rpv-loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:var(--muted-foreground);font-size:14px;background:#0A0E14;z-index:5;}
        .rpv-spinner{width:36px;height:36px;border:3px solid var(--border);border-top-color:var(--primary);border-radius:50%;animation:rpvspin 0.8s linear infinite;}
        @keyframes rpvspin{to{transform:rotate(360deg);}}
        .rpv-controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;background:var(--card);border:1px solid var(--border);border-radius:10px;margin-top:10px;}
        .rpv-btn{background:var(--background);border:1px solid var(--border);color:var(--foreground);border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer;font-weight:600;}
        .rpv-btn:hover{border-color:var(--primary);}
        .rpv-btn.active{background:var(--primary);border-color:var(--primary);}
        .rpv-seek{flex:1;min-width:160px;}
        .rpv-time{font-family:monospace;font-size:12px;color:var(--muted-foreground);white-space:nowrap;}
        .rpv-side{display:flex;flex-direction:column;gap:12px;max-height:calc(70vh + 62px);overflow-y:auto;}
        .rpv-panel{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px;}
        .rpv-panel h3{font-size:13px;font-weight:700;color:var(--foreground);margin:0 0 10px;}
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
      `}</style>

      <Link to="/replays" className="rpv-back">← Назад к списку</Link>
      <div className="rpv-top">
        <h2>
          🎬 {data?.replay.submarine_name ?? "Раунд"} #{replayId}
          {data?.replay.level_name && <span style={{ color: "var(--muted-foreground)", fontWeight: 400, fontSize: 14 }}> · {data.replay.level_name}</span>}
        </h2>
        <span className="rpv-time">{data?.replay.recorded_at ? new Date(data.replay.recorded_at).toLocaleString("ru-RU") : ""}</span>
      </div>

      <div className="rpv-layout">
        <div>
          <div className="rpv-canvas-wrap">
            <canvas id="rpvCanvas" />
            <div className="rpv-loading" id="rpvLoading">
              <div className="rpv-spinner" />
              <div id="rpvLoadingText">{scriptsReady ? "Загрузка реплея..." : "Загрузка плеера..."}</div>
            </div>
          </div>

          <div className="rpv-controls">
            <button className="rpv-btn" id="rpvPlay">▶</button>
            <span className="rpv-time" id="rpvTimeLabel">00:00 / 00:00</span>
            <input type="range" className="rpv-seek" id="rpvSeek" min={0} max={1000} defaultValue={0} />
            <div style={{ display: "flex", gap: 4 }}>
              <button className="rpv-btn rpv-speed" data-speed="0.5">0.5×</button>
              <button className="rpv-btn rpv-speed active" data-speed="1">1×</button>
              <button className="rpv-btn rpv-speed" data-speed="2">2×</button>
              <button className="rpv-btn rpv-speed" data-speed="4">4×</button>
            </div>
            <button className="rpv-btn" id="rpvFollow">📷 Следить за лодкой</button>
          </div>
        </div>

        <div className="rpv-side">
          <div className="rpv-panel">
            <h3>🔊 Звук</h3>
            <div className="rpv-row"><label><input type="checkbox" id="rpvVoiceEnable" /> Включить голос</label></div>
            <div className="rpv-row"><span>Общая громкость</span><input type="range" className="rpv-slider" id="rpvMasterVol" min={0} max={100} defaultValue={100} /></div>
            <div className="rpv-row"><label><input type="checkbox" id="rpvSpatial" defaultChecked /> Затухание по расстоянию</label></div>
            <div className="rpv-row"><span title="Дистанция, на которой голос затухает до нуля">Радиус слышимости</span><input type="range" className="rpv-slider" id="rpvFalloff" min={500} max={8000} step={100} defaultValue={3000} /></div>
          </div>
          <div className="rpv-panel">
            <h3>👥 Игроки</h3>
            <div className="rpv-players" id="rpvPlayers" />
          </div>
          <div className="rpv-panel">
            <h3>💬 Чат</h3>
            <div className="rpv-chat" id="rpvChat" />
          </div>
          {replayId && <ReplayLogsPanel replayId={replayId} />}
        </div>
      </div>
    </div>
  )
}
