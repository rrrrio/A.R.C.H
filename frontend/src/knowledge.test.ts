import { describe, expect, it } from 'vitest'

import { fetchKnowledgeStatus, rebuildKnowledgeIndex, readKnowledgeStatus } from './knowledge'

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

  it('posts a CSRF-protected request to rebuild the fixed local inbox index', async () => {
    const originalFetch = globalThis.fetch
    const requests: Array<{ input: string; init: RequestInit | undefined }> = []
    globalThis.fetch = async (input, init) => {
      requests.push({ input: String(input), init })
      return new Response(JSON.stringify({ changed_source_count: 2, source_count: 3, indexed_at: '2026-11-11T00:00:00+00:00' }), { status: 200 })
    }

    try {
      await expect(rebuildKnowledgeIndex('csrf-token')).resolves.toEqual({
        changed_source_count: 2,
        source_count: 3,
        indexed_at: '2026-11-11T00:00:00+00:00',
      })
    } finally {
      globalThis.fetch = originalFetch
    }

    expect(requests).toHaveLength(1)
    expect(requests[0].input).toBe('/api/knowledge/reindex')
    expect(requests[0].init?.method).toBe('POST')
    expect(new Headers(requests[0].init?.headers).get('X-CSRF-Token')).toBe('csrf-token')
  })
})
