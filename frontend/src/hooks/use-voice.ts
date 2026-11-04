import { useQuery } from '@tanstack/react-query'
import { fetchVoice, type VoiceStatus } from '@/lib/workforce'

const UNKNOWN: VoiceStatus = { stt: false, tts: false }

/** Čo server vie s hlasom — bez prepisu sa skryje mikrofón, bez syntézy prehrávanie. */
export function useVoice(): VoiceStatus {
  const { data } = useQuery({ queryKey: ['voice'], queryFn: fetchVoice, staleTime: Infinity })
  return data ?? UNKNOWN
}
