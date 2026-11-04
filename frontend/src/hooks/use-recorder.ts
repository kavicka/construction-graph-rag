import { useCallback, useEffect, useRef, useState } from 'react'

/** Formats in order of preference: Chrome/Android webm+opus, Safari/iOS mp4 (aac). */
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']

export type RecorderState = 'idle' | 'recording' | 'stopping'

export function recorderSupport(): string | null {
  if (typeof window === 'undefined') return 'nedostupné'
  if (!window.isSecureContext) return 'Mikrofón funguje len cez HTTPS (alebo localhost).'
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined')
    return 'Tento prehliadač nevie nahrávať zvuk.'
  return null
}

/** Microphone recording → Blob. `level` 0–1 drives visual feedback (the worker sees they are heard). */
export function useRecorder() {
  const [state, setState] = useState<RecorderState>('idle')
  const [seconds, setSeconds] = useState(0)
  const [level, setLevel] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const chunks = useRef<Blob[]>([])
  const resolveStop = useRef<((b: Blob | null) => void) | null>(null)
  const timers = useRef<{ tick?: number; raf?: number; ctx?: AudioContext }>({})

  const cleanup = useCallback(() => {
    window.clearInterval(timers.current.tick)
    if (timers.current.raf) cancelAnimationFrame(timers.current.raf)
    void timers.current.ctx?.close().catch(() => undefined)
    timers.current = {}
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
    setLevel(0)
  }, [])

  useEffect(() => cleanup, [cleanup])

  const start = useCallback(async () => {
    const unsupported = recorderSupport()
    if (unsupported) {
      setError(unsupported)
      return false
    }
    setError(null)
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      })
      stream.current = media
      const mimeType = MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m))
      const rec = new MediaRecorder(media, mimeType ? { mimeType } : undefined)
      chunks.current = []
      rec.ondataavailable = (e) => {
        if (e.data.size) chunks.current.push(e.data)
      }
      rec.onstop = () => {
        const blob = new Blob(chunks.current, { type: rec.mimeType || mimeType || 'audio/webm' })
        cleanup()
        setState('idle')
        resolveStop.current?.(blob.size ? blob : null)
        resolveStop.current = null
      }
      rec.start(250)
      recorder.current = rec
      setSeconds(0)
      setState('recording')
      const startedAt = Date.now()
      timers.current.tick = window.setInterval(
        () => setSeconds(Math.floor((Date.now() - startedAt) / 1000)),
        250,
      )
      // volume level for the animated indicator
      const ctx = new AudioContext()
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 512
      ctx.createMediaStreamSource(media).connect(analyser)
      const buf = new Uint8Array(analyser.fftSize)
      const loop = () => {
        analyser.getByteTimeDomainData(buf)
        let peak = 0
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128))
        setLevel(Math.min(1, peak / 64))
        timers.current.raf = requestAnimationFrame(loop)
      }
      timers.current.ctx = ctx
      loop()
      return true
    } catch (e) {
      cleanup()
      setState('idle')
      const name = e instanceof DOMException ? e.name : ''
      setError(
        name === 'NotAllowedError'
          ? 'Prístup k mikrofónu je zakázaný. Povoľ ho v nastaveniach prehliadača.'
          : name === 'NotFoundError'
            ? 'Nenašiel sa žiadny mikrofón.'
            : 'Mikrofón sa nepodarilo zapnúť.',
      )
      return false
    }
  }, [cleanup])

  const stop = useCallback((): Promise<Blob | null> => {
    const rec = recorder.current
    if (!rec || rec.state === 'inactive') return Promise.resolve(null)
    setState('stopping')
    return new Promise((resolve) => {
      resolveStop.current = resolve
      rec.stop()
    })
  }, [])

  const cancel = useCallback(() => {
    resolveStop.current = null
    const rec = recorder.current
    if (rec && rec.state !== 'inactive') {
      rec.onstop = () => {
        cleanup()
        setState('idle')
      }
      rec.stop()
    }
  }, [cleanup])

  return { state, seconds, level, error, start, stop, cancel }
}

export function formatSeconds(s: number): string {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
