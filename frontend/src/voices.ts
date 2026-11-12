export type VoiceProfile = {
  id: string
  name: string
  transcript: string
  sample_rate: number
  duration_ms: number
  created_at: string
}

export type VoiceProfiles = { selected_id: string | null; profiles: VoiceProfile[] }

const MAX_REFERENCE_BYTES = 5 * 1024 * 1024

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function readVoiceProfile(value: unknown): VoiceProfile | null {
  if (!isRecord(value) || typeof value.id !== 'string' || typeof value.name !== 'string' || typeof value.transcript !== 'string' || typeof value.sample_rate !== 'number' || typeof value.duration_ms !== 'number' || typeof value.created_at !== 'string') return null
  return { id: value.id, name: value.name, transcript: value.transcript, sample_rate: value.sample_rate, duration_ms: value.duration_ms, created_at: value.created_at }
}

function readProfiles(value: unknown): VoiceProfiles | null {
  if (!isRecord(value) || (value.selected_id !== null && typeof value.selected_id !== 'string') || !Array.isArray(value.profiles)) return null
  const profiles = value.profiles.map(readVoiceProfile)
  if (profiles.some((profile) => profile === null)) return null
  return { selected_id: value.selected_id, profiles: profiles as VoiceProfile[] }
}

export async function fetchVoiceProfiles(): Promise<VoiceProfiles> {
  const response = await fetch('/api/voices/profiles', { headers: { Accept: 'application/json' } })
  const profiles = readProfiles(await response.json())
  if (!response.ok || profiles === null) throw new Error('Voice profile response was invalid.')
  return profiles
}

export async function validateReferenceAudio(file: Blob): Promise<void> {
  if (file.size === 0) throw new Error('参考音频文件不能为空。')
  if (file.size > MAX_REFERENCE_BYTES) throw new Error('参考音频文件不能超过 5 MiB。')
}

export function handleReferenceFileSelection(
  input: Pick<HTMLInputElement, 'files' | 'value'>,
  onSelect: (file: File | null) => void,
): void {
  onSelect(input.files?.[0] ?? null)
}

export async function restartVoiceSessionForProfileSelection(
  voiceState: 'idle' | 'connecting' | 'preparing' | 'ready' | 'disconnected' | 'error',
  disconnect: () => Promise<void>,
  connect: () => Promise<void>,
): Promise<void> {
  if (voiceState !== 'connecting' && voiceState !== 'preparing' && voiceState !== 'ready') return
  await disconnect()
  await connect()
}

export async function transcribeReferenceAudio(csrfToken: string, referenceAudioBase64: string): Promise<string> {
  const response = await fetch('/api/voices/reference-transcript', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ reference_audio_base64: referenceAudioBase64 }),
  })
  const body = await readJson(response)
  if (!response.ok) throw new Error(readErrorDetail(body) ?? 'Reference transcription failed.')
  if (!isRecord(body) || typeof body.transcript !== 'string') throw new Error('Reference transcription returned invalid text.')
  return body.transcript
}

export async function createVoiceProfile(csrfToken: string, input: {
  name: string; transcript: string; referenceAudioBase64: string; ownershipConfirmed: boolean
}): Promise<VoiceProfile> {
  const response = await fetch('/api/voices/profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ name: input.name, transcript: input.transcript, reference_audio_base64: input.referenceAudioBase64, ownership_confirmed: input.ownershipConfirmed }),
  })
  const body = await readJson(response)
  if (!response.ok) throw new Error(readErrorDetail(body) ?? 'Voice profile creation failed.')
  const profile = readVoiceProfile(body)
  if (profile === null) throw new Error('Voice profile creation returned invalid metadata.')
  return profile
}

export async function selectVoiceProfile(csrfToken: string, profileId: string | null): Promise<VoiceProfiles> {
  const response = await fetch('/api/voices/selection', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ profile_id: profileId }),
  })
  const profiles = readProfiles(await response.json())
  if (!response.ok || profiles === null) throw new Error('Voice profile selection failed.')
  return profiles
}

export async function deleteVoiceProfile(csrfToken: string, profileId: string): Promise<void> {
  const response = await fetch(`/api/voices/profiles/${encodeURIComponent(profileId)}`, {
    method: 'DELETE', headers: { 'X-CSRF-Token': csrfToken },
  })
  if (!response.ok) throw new Error('Voice profile deletion failed.')
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function readErrorDetail(value: unknown): string | null {
  if (!isRecord(value)) return null
  if (typeof value.detail === 'string') return value.detail
  if (!Array.isArray(value.detail)) return null
  const messages = value.detail
    .filter(isRecord)
    .map((item) => typeof item.msg === 'string' ? item.msg : null)
    .filter((message): message is string => message !== null)
  return messages.length > 0 ? messages.join('；') : null
}
