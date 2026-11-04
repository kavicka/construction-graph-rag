import { useQuery } from '@tanstack/react-query'
import { fetchVoice, type VoiceStatus } from '@/lib/workforce'

const UNKNOWN: VoiceStatus = { stt: false, tts: false }

/** Server voice capabilities — no transcription hides the microphone, no synthesis hides playback. */
export function useVoice(): VoiceStatus {
  const { data } = useQuery({ queryKey: ['voice'], queryFn: fetchVoice, staleTime: Infinity })
  return data ?? UNKNOWN
}
