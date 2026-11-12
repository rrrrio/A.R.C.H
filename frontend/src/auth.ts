export type AuthStatus = {
  configured: boolean
  authenticated: boolean
  terminal_lease_expires_at?: string | null
  terminal_lease_scopes?: string[]
  terminal_profile?: 'observe' | 'assist' | 'operator' | null
}

export type LoginResult = {
  csrf_token: string
}

export type TerminalLeaseResult = {
  terminal_lease_expires_at: string
  terminal_lease_scopes: string[]
}

export function hasCurrentAuthContract(status: AuthStatus): boolean {
  return status.authenticated && Array.isArray(status.terminal_lease_scopes)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function readAuthStatus(value: unknown): AuthStatus | null {
  if (
    isRecord(value) &&
    typeof value.configured === 'boolean' &&
    typeof value.authenticated === 'boolean'
  ) {
    const result: AuthStatus = { configured: value.configured, authenticated: value.authenticated }
    if (value.terminal_lease_expires_at === null || typeof value.terminal_lease_expires_at === 'string') {
      result.terminal_lease_expires_at = value.terminal_lease_expires_at
    }
    if (Array.isArray(value.terminal_lease_scopes) && value.terminal_lease_scopes.every((item) => typeof item === 'string')) {
      result.terminal_lease_scopes = value.terminal_lease_scopes
    }
    if (value.terminal_profile === null || value.terminal_profile === 'observe' || value.terminal_profile === 'assist' || value.terminal_profile === 'operator') {
      result.terminal_profile = value.terminal_profile
    }
    return result
  }
  return null
}

export async function setTerminalProfile(
  csrfToken: string,
  profile: 'observe' | 'assist' | 'operator',
): Promise<AuthStatus> {
  const response = await fetch('/api/auth/terminal-profile', {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ profile }),
  })
  const payload: unknown = await response.json()
  const status = readAuthStatus(payload)
  if (!response.ok || status === null) throw new Error('Terminal profile update failed.')
  return status
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const response = await fetch('/api/auth/status', { headers: { Accept: 'application/json' } })
  const payload: unknown = await response.json()
  const status = readAuthStatus(payload)
  if (!response.ok || status === null) {
    throw new Error('Authentication status response was invalid.')
  }
  return status
}

export async function login(password: string): Promise<LoginResult> {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  const payload: unknown = await response.json()
  if (!response.ok || !isRecord(payload) || typeof payload.csrf_token !== 'string') {
    throw new Error('Login failed.')
  }
  return { csrf_token: payload.csrf_token }
}

export async function fetchCsrfToken(): Promise<string> {
  const response = await fetch('/api/auth/csrf', { headers: { Accept: 'application/json' } })
  const payload: unknown = await response.json()
  if (!response.ok || !isRecord(payload) || typeof payload.csrf_token !== 'string') {
    throw new Error('CSRF token response was invalid.')
  }
  return payload.csrf_token
}

export async function reissueTerminalLease(
  csrfToken: string,
  password: string,
): Promise<TerminalLeaseResult> {
  const response = await fetch('/api/auth/terminal-lease/reissue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ password }),
  })
  const payload: unknown = await response.json()
  if (
    !response.ok
    || !isRecord(payload)
    || typeof payload.terminal_lease_expires_at !== 'string'
    || !Array.isArray(payload.terminal_lease_scopes)
    || !payload.terminal_lease_scopes.every((item) => typeof item === 'string')
  ) {
    throw new Error('Terminal lease reissue failed.')
  }
  return {
    terminal_lease_expires_at: payload.terminal_lease_expires_at,
    terminal_lease_scopes: payload.terminal_lease_scopes,
  }
}

export async function logout(csrfToken: string): Promise<void> {
  const response = await fetch('/api/auth/logout', {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken },
  })
  if (!response.ok) {
    throw new Error('Logout failed.')
  }
}
