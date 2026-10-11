import type {
  AbsLibrary,
  AbsSettingsInfo,
  AudibleAccountInfo,
  ImportRunDetail,
  ImportRunSummary,
  LoginUrlResponse,
  Me,
  TenantUser,
} from './types'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      'X-Requested-With': 'audible-cli-webui',
      ...init?.headers,
    },
  })

  if (res.status === 401) {
    window.location.href = '/auth/login'
    throw new ApiError(401, 'Not authenticated')
  }

  if (!res.ok) {
    const body = await res.text()
    throw new ApiError(res.status, body || res.statusText)
  }

  if (res.status === 204) {
    return undefined as T
  }

  return (await res.json()) as T
}

const get = <T>(path: string) => request<T>(path)
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined })
const put = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'PUT', body: body !== undefined ? JSON.stringify(body) : undefined })
const del = <T>(path: string) => request<T>(path, { method: 'DELETE' })

export const api = {
  me: () => get<Me>('/api/me'),
  logout: () => post<void>('/auth/logout'),

  audibleAccount: () => get<AudibleAccountInfo>('/api/audible/account'),
  startAudibleConnect: (marketplace: string, withUsername: boolean) =>
    post<LoginUrlResponse>('/api/audible/connect/start', { marketplace, with_username: withUsername }),
  completeAudibleConnect: (pastedUrl: string) =>
    post<{ ok: boolean }>('/api/audible/connect/complete', { pasted_url: pastedUrl }),
  disconnectAudible: () => del<void>('/api/audible/account'),

  triggerImport: () => post<{ run_id: number }>('/api/imports'),
  listImports: () => get<ImportRunSummary[]>('/api/imports'),
  currentImport: () => get<ImportRunDetail | null>('/api/imports/current'),
  getImport: (runId: number) => get<ImportRunDetail>(`/api/imports/${runId}`),

  adminListUsers: () => get<TenantUser[]>('/api/admin/users'),
  adminGetAbsSettings: () => get<AbsSettingsInfo>('/api/admin/abs-settings'),
  adminSaveAbsSettings: (baseUrl: string, apiKey: string) =>
    put<AbsSettingsInfo>('/api/admin/abs-settings', { base_url: baseUrl, api_key: apiKey }),
  adminListAbsLibraries: () => get<AbsLibrary[]>('/api/admin/abs-libraries'),
  adminSetTenantMapping: (userId: number, absLibraryId: string, absFolderId: string) =>
    put<void>(`/api/admin/tenant-mappings/${userId}`, {
      abs_library_id: absLibraryId,
      abs_folder_id: absFolderId,
    }),
  adminListImports: (userId?: number) =>
    get<ImportRunSummary[]>(`/api/admin/imports${userId ? `?user_id=${userId}` : ''}`),
}
