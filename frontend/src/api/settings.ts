import { api } from './client';

export type ToneMapAlgorithm = 'reinhard' | 'aces' | 'clip';

export interface AppSettings {
  base_url: string;
  library_dir: string;
  thumbnail_dir: string;
  auth_disabled: boolean;
  import_rate_limit_ms: number;
  thumbnail_quality: number;
  preview_max_width: number;
  max_upload_size_mb: number;
  token_expiry_days: number;
  has_steam_api_key: boolean;
  has_steamgriddb_api_key: boolean;
  has_igdb_credentials: boolean;
  library_size: string;
  library_size_bytes: number;
  thumbnail_cache_size: string;
  thumbnail_cache_size_bytes: number;
  game_count: number;
  screenshot_count: number;
  annotation_count: number;
  active_share_count: number;
  import_session_count: number;
  specialk_path: string;
  tone_map_algorithm: ToneMapAlgorithm;
  tone_map_exposure: number;
}

export interface PreferenceUpdate {
  specialk_path?: string;
  tone_map_algorithm?: ToneMapAlgorithm;
  tone_map_exposure?: number;
}

export async function savePreferences(
  prefs: PreferenceUpdate,
): Promise<{ message: string }> {
  return api.put('/settings/preferences', prefs);
}

export async function getSettings(): Promise<AppSettings> {
  return api.get('/settings');
}

export interface ThumbnailMigrationResult {
  moved: number;
  already_cached: number;
  missing: number;
  invalid: number;
}

export async function migrateThumbnailCache(): Promise<ThumbnailMigrationResult> {
  return api.post('/settings/thumbnail-cache/migrate');
}

export interface PreviewBackfillJob {
  id: number;
  status: 'pending' | 'running' | 'completed' | 'failed';
  total: number;
  processed: number;
  errors: number;
  error_message: string | null;
}

export async function getPreviewBackfill(): Promise<PreviewBackfillJob | null> {
  return api.get('/settings/preview-cache/backfill');
}

export async function startPreviewBackfill(): Promise<PreviewBackfillJob> {
  return api.post('/settings/preview-cache/backfill');
}

export interface PerformanceMetric {
  stage: 'database' | 'image' | 'import' | 'media';
  operation: string;
  count: number;
  average_ms: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
  threshold_ms: number;
  slow_count: number;
  error_count: number;
  recent_error_count: number;
  total_bytes: number;
  last_seen_at: string;
}

export interface PerformanceSnapshot {
  started_at: string;
  generated_at: string;
  sample_limit: number;
  total_measurements: number;
  metrics: PerformanceMetric[];
}

export async function getPerformanceDiagnostics(): Promise<PerformanceSnapshot> {
  return api.get('/settings/performance');
}

export async function resetPerformanceDiagnostics(): Promise<PerformanceSnapshot> {
  return api.delete('/settings/performance');
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
