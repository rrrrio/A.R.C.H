import { describe, expect, it } from 'vitest'

import { fetchMemoryCandidates, readMemoryCandidate } from './memory'

describe('readMemoryCandidate', () => {
  it('accepts a bounded candidate payload', () => {
    expect(readMemoryCandidate({
      id: 'candidate-1', source_message_id: 2, content: '喜欢简洁回答', status: 'pending',
      created_at: '2026-11-08T00:00:00Z', updated_at: '2026-11-08T00:00:00Z',
    })).toMatchObject({ id: 'candidate-1', status: 'pending' })
  })

  it('rejects unrecognized candidate states', () => {
    expect(readMemoryCandidate({
      id: 'candidate-1', source_message_id: 2, content: '喜欢简洁回答', status: 'active',
      created_at: '2026-11-08T00:00:00Z', updated_at: '2026-11-08T00:00:00Z',
    })).toBeNull()
  })

  it('requests the selected review status', async () => {
    const originalFetch = globalThis.fetch
    const requests: string[] = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return new Response('[]', { status: 200 })
    }

    try {
      await fetchMemoryCandidates('approved')
    } finally {
      globalThis.fetch = originalFetch
    }

    expect(requests).toEqual(['/api/memory/candidates?status=approved'])
  })
})
