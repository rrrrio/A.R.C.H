import { describe, expect, it } from 'vitest'

import { fetchKnowledgeStatus, readKnowledgeStatus } from './knowledge'

describe('readKnowledgeStatus', () => {
  it('accepts a browser-safe local index summary', () => {
    expect(readKnowledgeStatus({ source_count: 3, indexed_at: '2026-11-08T00:00:00+00:00' })).toEqual({
      source_count: 3,
      indexed_at: '2026-11-08T00:00:00+00:00',
    })
  })

  it('rejects negative or malformed source counts', () => {
    expect(readKnowledgeStatus({ source_count: -1, indexed_at: null })).toBeNull()
    expect(readKnowledgeStatus({ source_count: '1', indexed_at: null })).toBeNull()
  })

  it('requests only the read-only status endpoint', async () => {
    const originalFetch = globalThis.fetch
    const requests: string[] = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return new Response(JSON.stringify({ source_count: 0, indexed_at: null }), { status: 200 })
    }

    try {
      await fetchKnowledgeStatus()
    } finally {
      globalThis.fetch = originalFetch
    }

    expect(requests).toEqual(['/api/knowledge/status'])
  })
})
