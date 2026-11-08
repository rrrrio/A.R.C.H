export type KnowledgeStatus = {
  source_count: number
  indexed_at: string | null
}

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
