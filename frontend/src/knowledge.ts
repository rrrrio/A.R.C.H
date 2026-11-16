export type KnowledgeStatus = {
  source_count: number
  indexed_at: string | null
}

export type KnowledgeReindexResult = KnowledgeStatus & {
  changed_source_count: number
}

export type KnowledgeSource = { path: string; updated_at: string }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function readKnowledgeStatus(value: unknown): KnowledgeStatus | null {
  if (
    !isRecord(value) ||
    typeof value.source_count !== 'number' ||
    !Number.isInteger(value.source_count) ||
    value.source_count < 0 ||
    (value.indexed_at !== null && typeof value.indexed_at !== 'string')
  ) {
    return null
  }
  return { source_count: value.source_count, indexed_at: value.indexed_at }
}

export async function fetchKnowledgeStatus(): Promise<KnowledgeStatus> {
  const response = await fetch('/api/knowledge/status', { headers: { Accept: 'application/json' } })
  const status = readKnowledgeStatus(await response.json())
  if (!response.ok || status === null) throw new Error('Knowledge status response was invalid.')
  return status
}

export async function fetchKnowledgeSources(query = ''): Promise<KnowledgeSource[]> {
  const response = await fetch(`/api/knowledge/sources?query=${encodeURIComponent(query)}`, { headers: { Accept: 'application/json' } })
  const payload: unknown = await response.json()
  if (!response.ok || !Array.isArray(payload) || payload.some((item) => !isRecord(item) || typeof item.path !== 'string' || typeof item.updated_at !== 'string')) throw new Error('Knowledge source response was invalid.')
  return payload as KnowledgeSource[]
}

export async function rebuildKnowledgeIndex(csrfToken: string): Promise<KnowledgeReindexResult> {
  const response = await fetch('/api/knowledge/reindex', {
    method: 'POST',
    headers: { Accept: 'application/json', 'X-CSRF-Token': csrfToken },
  })
  const payload: unknown = await response.json()
  if (
    !response.ok ||
    !isRecord(payload) ||
    typeof payload.changed_source_count !== 'number' ||
    !Number.isInteger(payload.changed_source_count) ||
    payload.changed_source_count < 0
  ) {
    throw new Error('Knowledge rebuild response was invalid.')
  }
  const status = readKnowledgeStatus(payload)
  if (status === null) throw new Error('Knowledge rebuild response was invalid.')
  return { ...status, changed_source_count: payload.changed_source_count }
}
