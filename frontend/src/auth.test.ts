import { afterEach, describe, expect, it, vi } from 'vitest'

import { hasCurrentAuthContract, readAuthStatus, reissueTerminalLease } from './auth'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('readAuthStatus', () => {
  it('accepts the authentication status payload', () => {
    expect(readAuthStatus({ authenticated: true, configured: true })).toEqual({
      authenticated: true,
      configured: true,
    })
  })

  it('accepts terminal lease metadata without exposing a lease token', () => {
    expect(readAuthStatus({
      authenticated: true,
      configured: true,
      terminal_lease_expires_at: '2026-11-11T12:30:00+00:00',
      terminal_lease_scopes: ['registered_observe', 'home_metadata'],
    })).toEqual({
      authenticated: true,
      configured: true,
      terminal_lease_expires_at: '2026-11-11T12:30:00+00:00',
      terminal_lease_scopes: ['registered_observe', 'home_metadata'],
    })
  })

  it('rejects malformed authentication status payloads', () => {
    expect(readAuthStatus({ authenticated: 'yes', configured: true })).toBeNull()
  })

  it('rejects an authenticated response from a backend without lease metadata', () => {
    const status = readAuthStatus({ authenticated: true, configured: true })
    expect(status).not.toBeNull()
    expect(hasCurrentAuthContract(status!)).toBe(false)
  })
})

describe('reissueTerminalLease', () => {
  it('posts the password with the current CSRF token and parses lease metadata', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          terminal_lease_expires_at: '2026-11-12T12:30:00+00:00',
          terminal_lease_scopes: ['registered_observe', 'home_metadata'],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(reissueTerminalLease('csrf-1', 'password-1')).resolves.toEqual({
      terminal_lease_expires_at: '2026-11-12T12:30:00+00:00',
      terminal_lease_scopes: ['registered_observe', 'home_metadata'],
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/terminal-lease/reissue', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf-1' },
      body: JSON.stringify({ password: 'password-1' }),
    }))
  })
})
