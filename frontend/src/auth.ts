export type AuthStatus = {
  configured: boolean
  authenticated: boolean
}

export type LoginResult = {
  csrf_token: string
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
    return { configured: value.configured, authenticated: value.authenticated }
  }
  return null
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

export async function logout(csrfToken: string): Promise<void> {
  const response = await fetch('/api/auth/logout', {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken },
  })
  if (!response.ok) {
    throw new Error('Logout failed.')
  }
}
