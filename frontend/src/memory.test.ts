import { describe, expect, it } from 'vitest'

import { readMemoryCandidate } from './memory'

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
})
