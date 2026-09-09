import { useEffect, useRef, useState } from "react"
import { Loader2, Play, Square } from "lucide-react"
import { Button } from "@/components/ui/button"

interface NdvcPacket {
  timeMs: number
  data: Uint8Array
}

interface VoiceClipPlayerProps {
  audioUrl: string
}

function parseNdvc(buffer: ArrayBuffer): NdvcPacket[] {
  const view = new DataView(buffer)
  if (view.byteLength < 9 || new TextDecoder().decode(new Uint8Array(buffer, 0, 4)) !== "NDVC") {
    throw new Error("Некорректный архив голосового фрагмента")
  }
  const version = view.getUint8(4)
  if (version !== 1) throw new Error(`Неподдерживаемая версия аудио: ${version}`)
  const count = view.getUint32(5, true)
  let offset = 9
  const packets: NdvcPacket[] = []
  for (let index = 0; index < count; index += 1) {
    if (offset + 6 > view.byteLength) throw new Error("Повреждённый голосовой архив")
    const timeMs = view.getUint32(offset, true)
    const length = view.getUint16(offset + 4, true)
    offset += 6
    if (offset + length > view.byteLength) throw new Error("Повреждённый голосовой архив")
    packets.push({ timeMs, data: new Uint8Array(buffer.slice(offset, offset + length)) })
    offset += length
  }
  if (offset !== view.byteLength) throw new Error("В голосовом архиве есть лишние данные")
  return packets
}

type DecoderConstructor = new (options: {
  output: (frame: any) => void
  error: (error: any) => void
}) => any

interface WebCodecsWindow extends Window {
  AudioDecoder?: {
    isConfigSupported: (config: unknown) => Promise<{ supported?: boolean }>
  }
  EncodedAudioChunk?: new (init: { type: string; timestamp: number; data: Uint8Array }) => unknown
}

export function VoiceClipPlayer({ audioUrl }: VoiceClipPlayerProps) {
  const [playing, setPlaying] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const tokenRef = useRef(0)
  const contextRef = useRef<AudioContext | null>(null)
  const decoderRef = useRef<any>(null)
  const sourcesRef = useRef<AudioBufferSourceNode[]>([])

  const stop = () => {
    tokenRef.current += 1
    sourcesRef.current.forEach((source) => {
      try { source.stop() } catch { /* already stopped */ }
    })
    sourcesRef.current = []
    try { decoderRef.current?.close() } catch { /* already closed */ }
    decoderRef.current = null
    const context = contextRef.current
    contextRef.current = null
    if (context) void context.close()
    setPlaying(false)
    setLoading(false)
  }

  const play = async () => {
    stop()
    const token = tokenRef.current
    setError(null)
    setLoading(true)
    try {
      const response = await fetch(audioUrl, { credentials: "include" })
      if (!response.ok) throw new Error(`Не удалось загрузить аудио (${response.status})`)
      const packets = parseNdvc(await response.arrayBuffer())
      const browser = window as WebCodecsWindow
      const decoderConstructor = browser.AudioDecoder as DecoderConstructor | undefined
      const chunkConstructor = browser.EncodedAudioChunk
      if (!decoderConstructor || !chunkConstructor) {
        throw new Error("Браузер не поддерживает декодирование Opus (WebCodecs AudioDecoder)")
      }
      const support = await browser.AudioDecoder?.isConfigSupported({
        codec: "opus",
        sampleRate: 48000,
        numberOfChannels: 1,
      })
      if (!support?.supported) throw new Error("Браузер не поддерживает декодирование Opus")
      if (token !== tokenRef.current) return

      const context = new AudioContext({ sampleRate: 48000 })
      contextRef.current = context
      let playhead = context.currentTime + 0.08
      const decoder = new decoderConstructor({
        output: (frame: any) => {
          if (token !== tokenRef.current) {
            frame.close()
            return
          }
          const frameCount = frame.numberOfFrames
          const sampleRate = frame.sampleRate || 48000
          const pcm = new Float32Array(frameCount)
          frame.copyTo(pcm, { planeIndex: 0, format: "f32-planar" })
          frame.close()
          const audioBuffer = context.createBuffer(1, frameCount, sampleRate)
          audioBuffer.getChannelData(0).set(pcm)
          const source = context.createBufferSource()
          source.buffer = audioBuffer
          source.connect(context.destination)
          source.start(playhead)
          sourcesRef.current.push(source)
          playhead += frameCount / sampleRate
        },
        error: () => setError("Не удалось декодировать голосовой фрагмент"),
      })
      decoder.configure({ codec: "opus", sampleRate: 48000, numberOfChannels: 1 })
      decoderRef.current = decoder
      packets.forEach((packet) => {
        decoder.decode(new chunkConstructor({
          type: "key",
          timestamp: packet.timeMs * 1000,
          data: packet.data,
        }))
      })
      await decoder.flush()
      if (token !== tokenRef.current) return
      setPlaying(true)
      window.setTimeout(() => {
        if (token === tokenRef.current) stop()
      }, Math.max(100, (playhead - context.currentTime) * 1000 + 120))
    } catch (caught) {
      if (token === tokenRef.current) {
        setError(caught instanceof Error ? caught.message : "Не удалось воспроизвести аудио")
        stop()
      }
    } finally {
      if (token === tokenRef.current) setLoading(false)
    }
  }

  useEffect(() => stop, [])

  return (
    <div className="flex flex-col items-end gap-1">
      <Button size="sm" variant="outline" onClick={() => (playing ? stop() : void play())} disabled={loading}>
        {loading ? <Loader2 className="animate-spin" /> : playing ? <Square /> : <Play />}
        {loading ? "Загрузка" : playing ? "Стоп" : "Слушать"}
      </Button>
      {error && <span className="max-w-52 text-right text-xs text-destructive">{error}</span>}
    </div>
  )
}
