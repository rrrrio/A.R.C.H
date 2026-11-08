export type HealthResponse = {
  environment: string
  status: 'ok'
}

export function readHealthResponse(value: unknown): HealthResponse | null {
  if (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    'environment' in value &&
    value.status === 'ok' &&
    typeof value.environment === 'string'
  ) {
    return value as HealthResponse
  }

  return null
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch('/api/health', { headers: { Accept: 'application/json' } })
  const payload: unknown = await response.json()
  const health = readHealthResponse(payload)

  if (!response.ok || health === null) {
    throw new Error('Backend health check returned an invalid response.')
  }

  return health
}
