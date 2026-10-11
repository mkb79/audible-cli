export interface Me {
  id: number
  display_name: string | null
  email: string | null
  is_admin: boolean
}

export type AudibleAccountStatus = 'active' | 'needs_reconnect'

export interface AudibleAccountInfo {
  connected: boolean
  marketplace: string | null
  with_username: boolean
  status: AudibleAccountStatus | null
  connected_at: string | null
}

export interface LoginUrlResponse {
  login_url: string
}

export type ImportRunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type ImportRunItemStatus =
  | 'pending'
  | 'downloading'
  | 'decrypting'
  | 'uploading'
  | 'succeeded'
  | 'failed'
  | 'skipped'

export interface ImportRunItem {
  id: number
  asin: string
  title: string
  status: ImportRunItemStatus
  error_message: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ImportRunSummary {
  id: number
  user_id: number
  status: ImportRunStatus
  requested_at: string
  started_at: string | null
  finished_at: string | null
  total_items: number | null
  error_message: string | null
}

export interface ImportRunDetail extends ImportRunSummary {
  items: ImportRunItem[]
}

export interface AbsSettingsInfo {
  base_url: string
  api_key_set: boolean
  default_library_id: string | null
  default_folder_id: string | null
}

export interface ImportReadiness {
  ready: boolean
  reason: string | null
}

export interface AbsLibraryFolder {
  id: string
  name: string
}

export interface AbsLibrary {
  id: string
  name: string
  media_type: 'book' | 'podcast'
  folders: AbsLibraryFolder[]
}

export interface TenantUser {
  id: number
  display_name: string | null
  email: string | null
  is_admin: boolean
  audible_connected: boolean
  abs_library_id: string | null
  abs_folder_id: string | null
}
