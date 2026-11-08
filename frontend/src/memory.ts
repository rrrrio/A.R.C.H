export type MemoryCandidate = {
  id: string
  source_message_id: number
  content: string
  status: 'pending' | 'approved' | 'rejected' | 'deleted'
  created_at: string
  updated_at: string
}

export type VisibleMemoryStatus = 'pending' | 'approved'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function readMemoryCandidate(value: unknown): MemoryCandidate | null {
  if (
    !isRecord(value) ||
    typeof value.id !== 'string' ||
    typeof value.source_message_id !== 'number' ||
    typeof value.content !== 'string' ||
    (value.status !== 'pending' && value.status !== 'approved' && value.status !== 'rejected' && value.status !== 'deleted') ||
    typeof value.created_at !== 'string' ||
    typeof value.updated_at !== 'string'
  ) {
    return null
  }
  return {
    id: value.id,
    source_message_id: value.source_message_id,
    content: value.content,
    status: value.status,
    created_at: value.created_at,
    updated_at: value.updated_at,
  }
}

export async function fetchMemoryCandidates(status: VisibleMemoryStatus = 'pending'): Promise<MemoryCandidate[]> {
  const response = await fetch(`/api/memory/candidates?status=${status}`, { headers: { Accept: 'application/json' } })
  const payload: unknown = await response.json()
  if (!response.ok || !Array.isArray(payload)) throw new Error('Memory candidate response was invalid.')
  const candidates = payload.map(readMemoryCandidate)
  if (candidates.some((candidate) => candidate === null)) throw new Error('Memory candidate response was invalid.')
  return candidates as MemoryCandidate[]
}

export async function updateMemoryCandidate(
  csrfToken: string,
  candidateId: string,
  update: { content?: string; action?: 'approved' | 'rejected' | 'deleted' },
): Promise<MemoryCandidate> {
  const response = await fetch(`/api/memory/candidates/${encodeURIComponent(candidateId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify(update),
  })
  const candidate = readMemoryCandidate(await response.json())
  if (!response.ok || candidate === null) throw new Error('Memory candidate update failed.')
  return candidate
}
