import { describe, expect, it } from 'vitest'

import { createConversationProfile, readConversationProfile, selectConversationProfile } from './conversationProfiles'

const input = { name: '小雅', enabled: true, persona: '温和回应用户。', speaking_style: '口语化。', examples: [], social_temperature: 0.7 }

describe('conversation profiles', () => {
  it('accepts bounded browser-safe profile metadata', () => {
    expect(readConversationProfile({ id: 'profile-1', ...input, version: 1, created_at: '2026-11-18T00:00:00Z', updated_at: '2026-11-18T00:00:00Z' })).toMatchObject({ id: 'profile-1', examples: [] })
  })

  it('rejects an incomplete profile response', () => {
    expect(readConversationProfile({ id: 'profile-1', name: '小雅' })).toBeNull()
  })

  it('sends a CSRF-protected neutral baseline selection', async () => {
    const originalFetch = globalThis.fetch
    let request: RequestInit | undefined
    globalThis.fetch = async (_, init) => { request = init; return new Response(JSON.stringify({ selected_id: null, profiles: [] })) }
    try { await selectConversationProfile('csrf', null) } finally { globalThis.fetch = originalFetch }
    expect(request?.headers).toMatchObject({ 'X-CSRF-Token': 'csrf' })
    expect(request?.body).toBe(JSON.stringify({ profile_id: null }))
  })

  it('preserves server validation detail when profile creation fails', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'Persona cannot be empty.' }), { status: 422 })
    try { await expect(createConversationProfile('csrf', input)).rejects.toThrow('Persona cannot be empty.') } finally { globalThis.fetch = originalFetch }
  })
})
