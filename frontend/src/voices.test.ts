import { describe, expect, it } from 'vitest'

import { readVoiceProfile, selectVoiceProfile } from './voices'

describe('voice profiles', () => {
  it('accepts metadata without a filesystem path or audio payload', () => {
    expect(readVoiceProfile({ id: 'profile-1', name: '我的声音', transcript: '参考文本', sample_rate: 24000, duration_ms: 3200, created_at: '2026-11-10T00:00:00Z' })).toMatchObject({ id: 'profile-1' })
  })

  it('rejects metadata that exposes an incomplete profile', () => {
    expect(readVoiceProfile({ id: 'profile-1', name: '我的声音', transcript: '参考文本' })).toBeNull()
  })

  it('sends an explicit baseline selection', async () => {
    const originalFetch = globalThis.fetch
    const requests: RequestInit[] = []
    globalThis.fetch = async (_, init) => {
      requests.push(init ?? {})
      return new Response(JSON.stringify({ selected_id: null, profiles: [] }), { status: 200 })
    }
    try {
      await selectVoiceProfile('csrf', null)
    } finally {
      globalThis.fetch = originalFetch
    }
    expect(requests[0]?.body).toBe(JSON.stringify({ profile_id: null }))
  })
})
