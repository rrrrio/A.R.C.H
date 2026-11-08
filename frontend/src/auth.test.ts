import { describe, expect, it } from 'vitest'

import { readAuthStatus } from './auth'

describe('readAuthStatus', () => {
  it('accepts the authentication status payload', () => {
    expect(readAuthStatus({ authenticated: true, configured: true })).toEqual({
      authenticated: true,
      configured: true,
    })
  })

  it('rejects malformed authentication status payloads', () => {
    expect(readAuthStatus({ authenticated: 'yes', configured: true })).toBeNull()
  })
})
