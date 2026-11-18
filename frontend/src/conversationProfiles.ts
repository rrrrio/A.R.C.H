export type ConversationExample = { user: string; assistant: string }

export type ConversationProfile = {
  id: string
  name: string
  enabled: boolean
  persona: string
  speaking_style: string
  examples: ConversationExample[]
  social_temperature: number
  version: number
  created_at: string
  updated_at: string
}

export type ConversationProfiles = { selected_id: string | null; profiles: ConversationProfile[] }

export type ConversationProfileInput = Omit<ConversationProfile, 'id' | 'version' | 'created_at' | 'updated_at'>

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function readExample(value: unknown): ConversationExample | null {
  if (!isRecord(value) || typeof value.user !== 'string' || typeof value.assistant !== 'string') return null
  return { user: value.user, assistant: value.assistant }
}

export function readConversationProfile(value: unknown): ConversationProfile | null {
  if (!isRecord(value) || typeof value.id !== 'string' || typeof value.name !== 'string' || typeof value.enabled !== 'boolean' || typeof value.persona !== 'string' || typeof value.speaking_style !== 'string' || !Array.isArray(value.examples) || typeof value.social_temperature !== 'number' || typeof value.version !== 'number' || typeof value.created_at !== 'string' || typeof value.updated_at !== 'string') return null
  const examples = value.examples.map(readExample)
  if (examples.some((example) => example === null)) return null
  return { id: value.id, name: value.name, enabled: value.enabled, persona: value.persona, speaking_style: value.speaking_style, examples: examples as ConversationExample[], social_temperature: value.social_temperature, version: value.version, created_at: value.created_at, updated_at: value.updated_at }
}

function readProfiles(value: unknown): ConversationProfiles | null {
  if (!isRecord(value) || (value.selected_id !== null && typeof value.selected_id !== 'string') || !Array.isArray(value.profiles)) return null
  const profiles = value.profiles.map(readConversationProfile)
  if (profiles.some((profile) => profile === null)) return null
  return { selected_id: value.selected_id, profiles: profiles as ConversationProfile[] }
}

export async function fetchConversationProfiles(): Promise<ConversationProfiles> {
  const response = await fetch('/api/conversation/profiles', { headers: { Accept: 'application/json' } })
  const profiles = readProfiles(await readJson(response))
  if (!response.ok || profiles === null) throw new Error('Conversation profile response was invalid.')
  return profiles
}

export async function createConversationProfile(csrfToken: string, input: ConversationProfileInput): Promise<ConversationProfile> {
  return writeProfile('/api/conversation/profiles', 'POST', csrfToken, input)
}

export async function replaceConversationProfile(csrfToken: string, profileId: string, input: ConversationProfileInput): Promise<ConversationProfile> {
  return writeProfile(`/api/conversation/profiles/${encodeURIComponent(profileId)}`, 'PUT', csrfToken, input)
}

export async function selectConversationProfile(csrfToken: string, profileId: string | null): Promise<ConversationProfiles> {
  const response = await fetch('/api/conversation/selection', {
    method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken }, body: JSON.stringify({ profile_id: profileId }),
  })
  const body = await readJson(response)
  const profiles = readProfiles(body)
  if (!response.ok || profiles === null) throw new Error(readErrorDetail(body) ?? 'Conversation profile selection failed.')
  return profiles
}

export async function deleteConversationProfile(csrfToken: string, profileId: string): Promise<void> {
  const response = await fetch(`/api/conversation/profiles/${encodeURIComponent(profileId)}`, { method: 'DELETE', headers: { 'X-CSRF-Token': csrfToken } })
  if (!response.ok) throw new Error(readErrorDetail(await readJson(response)) ?? 'Conversation profile deletion failed.')
}

async function writeProfile(url: string, method: 'POST' | 'PUT', csrfToken: string, input: ConversationProfileInput): Promise<ConversationProfile> {
  const response = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken }, body: JSON.stringify(input),
  })
  const body = await readJson(response)
  if (!response.ok) throw new Error(readErrorDetail(body) ?? 'Conversation profile save failed.')
  const profile = readConversationProfile(body)
  if (profile === null) throw new Error('Conversation profile save returned invalid metadata.')
  return profile
}

async function readJson(response: Response): Promise<unknown> {
  try { return await response.json() } catch { return null }
}

function readErrorDetail(value: unknown): string | null {
  if (!isRecord(value)) return null
  if (typeof value.detail === 'string') return value.detail
  if (!Array.isArray(value.detail)) return null
  const messages = value.detail.filter(isRecord).map((item) => typeof item.msg === 'string' ? item.msg : null).filter((message): message is string => message !== null)
  return messages.length ? messages.join('；') : null
}
