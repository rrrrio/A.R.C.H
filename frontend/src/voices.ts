export type VoiceProfile = {
  id: string
  name: string
  transcript: string
  sample_rate: number
  duration_ms: number
  created_at: string
}

export type VoiceProfiles = { selected_id: string | null; profiles: VoiceProfile[] }

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

export async function createVoiceProfile(csrfToken: string, input: {
  name: string; transcript: string; referenceWavBase64: string; ownershipConfirmed: boolean
}): Promise<VoiceProfile> {
  const response = await fetch('/api/voices/profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ name: input.name, transcript: input.transcript, reference_wav_base64: input.referenceWavBase64, ownership_confirmed: input.ownershipConfirmed }),
  })
  const profile = readVoiceProfile(await response.json())
  if (!response.ok || profile === null) throw new Error('Voice profile creation failed.')
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
