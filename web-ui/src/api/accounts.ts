import { http } from '@/lib/http'

export interface AccountItem {
  name: string
  path: string
}

export interface AccountDetail extends AccountItem {
  content: string
}

export type BrowserLoginStatus =
  | 'launching'
  | 'awaiting_scan'
  | 'saving'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface BrowserLoginJob {
  id: string
  account_name: string
  status: BrowserLoginStatus
  message: string
  created_at: string
  updated_at: string
  finished_at?: string | null
  error?: string | null
  account_path: string
  set_as_default: boolean
  default_state_path?: string | null
  browser_opened: boolean
}

export async function listAccounts(): Promise<AccountItem[]> {
  return await http('/api/accounts')
}

export async function getAccount(name: string): Promise<AccountDetail> {
  return await http(`/api/accounts/${encodeURIComponent(name)}`)
}

export async function createAccount(payload: { name: string; content: string }): Promise<AccountDetail> {
  return await http('/api/accounts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function updateAccount(name: string, content: string): Promise<AccountDetail> {
  return await http(`/api/accounts/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export async function deleteAccount(name: string): Promise<{ message: string }> {
  return await http(`/api/accounts/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export async function startBrowserLogin(payload: {
  name: string
  set_as_default: boolean
}): Promise<BrowserLoginJob> {
  return await http('/api/accounts/browser-login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function getBrowserLoginJob(jobId: string): Promise<BrowserLoginJob> {
  return await http(`/api/accounts/browser-login/${encodeURIComponent(jobId)}`)
}
