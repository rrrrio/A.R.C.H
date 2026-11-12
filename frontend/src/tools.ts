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

export type TerminalGrant = {
  id: string
  tool_name: 'terminal_exec' | 'terminal_shell'
  working_directory: string
  access: 'read' | 'write'
  capabilities: string[]
  created_at: string
  expires_at: string
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

export async function fetchTerminalGrants(): Promise<TerminalGrant[]> {
  const response = await fetch('/api/tools/grants', { credentials: 'same-origin' })
  if (!response.ok) throw new Error('Unable to load terminal grants.')
  return response.json() as Promise<TerminalGrant[]>
}

export async function revokeTerminalGrant(csrfToken: string, grantId: string): Promise<void> {
  const response = await fetch(`/api/tools/grants/${grantId}`, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: { 'X-CSRF-Token': csrfToken },
  })
  if (!response.ok) throw new Error('Unable to revoke terminal grant.')
}
