export type ToolApproval = {
  id: string
  tool_name: string
  argv_json: string
  working_directory: string
  reason: string
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'consumed'
  created_at: string
  expires_at: string
  decided_at: string | null
  decided_by: string | null
  outcome: 'completed' | 'approval_required' | 'denied' | null
  output: string | null
}

export async function fetchToolApprovals(): Promise<ToolApproval[]> {
  const response = await fetch('/api/tools/approvals', { credentials: 'same-origin' })
  if (!response.ok) throw new Error('Unable to load tool approvals.')
  return response.json() as Promise<ToolApproval[]>
}

export async function decideToolApproval(
  csrfToken: string,
  approvalId: string,
  action: 'approved' | 'rejected',
): Promise<ToolApproval> {
  const response = await fetch(`/api/tools/approvals/${approvalId}`, {
    method: 'PATCH',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ action }),
  })
  if (!response.ok) throw new Error('Unable to decide tool approval.')
  return response.json() as Promise<ToolApproval>
}
