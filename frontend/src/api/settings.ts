import { api } from './client';

export interface AppSettings {
  base_url: string;
  library_dir: string;
  auth_disabled: boolean;
  import_rate_limit_ms: number;
  thumbnail_quality: number;
  max_upload_size_mb: number;
  token_expiry_days: number;
  has_steam_api_key: boolean;
  has_steamgriddb_api_key: boolean;
  has_igdb_credentials: boolean;
  library_size: string;
  library_size_bytes: number;
  game_count: number;
  screenshot_count: number;
  annotation_count: number;
  active_share_count: number;
  import_session_count: number;
}

export async function getSettings(): Promise<AppSettings> {
  return api.get('/settings');
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ message: string }> {
  return api.post('/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

export async function saveApiKey(
  keyName: string,
  value: string,
): Promise<{ message: string }> {
  return api.put('/settings/api-keys', {
    key_name: keyName,
    value,
  });
}

export async function deleteApiKey(
  keyName: string,
): Promise<{ message: string }> {
  return api.delete(`/settings/api-keys/${keyName}`);
}
