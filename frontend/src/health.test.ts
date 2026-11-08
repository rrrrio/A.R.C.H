import { describe, expect, it } from 'vitest'

import { readHealthResponse } from './health'

describe('readHealthResponse', () => {
  it('accepts the backend health payload', () => {
    expect(readHealthResponse({ environment: 'development', status: 'ok' })).toEqual({
      environment: 'development',
      status: 'ok',
    })
  })

  it('rejects malformed payloads', () => {
    expect(readHealthResponse({ status: 'offline' })).toBeNull()
  })
})
