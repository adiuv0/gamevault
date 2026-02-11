import { api, tokenQs } from './client';

export interface SteamValidateResponse {
  valid: boolean;
  profile_name: string | null;
  avatar_url: string | null;
  is_numeric_id: boolean;
  error: string | null;
}

export interface SteamGameInfo {
  app_id: number;
  name: string;
  screenshot_count: number;
}

export interface SteamCredentials {
  user_id: string;
  steam_login_secure?: string;
  session_id?: string;
}

export interface SteamApiKeyStatus {
  has_api_key: boolean;
}

export async function checkApiKeyStatus(): Promise<SteamApiKeyStatus> {
  return api.get('/steam/api-key-status');
}

export async function validateSteam(data: SteamCredentials): Promise<SteamValidateResponse> {
  return api.post('/steam/validate', data);
}

export async function listSteamGames(data: SteamCredentials): Promise<SteamGameInfo[]> {
  return api.post('/steam/games', data);
}

export async function startSteamImport(data: {
  user_id: string;
  steam_login_secure?: string;
  session_id?: string;
  game_ids?: number[];
  is_numeric_id?: boolean;
}): Promise<{ session_id: number }> {
  return api.post('/steam/import', data);
}

export function connectImportProgress(
  sessionId: number,
  onEvent: (event: string, data: unknown) => void,
  onError?: (e: Event) => void,
): EventSource {
  const baseUrl = '/api';
  const url = `${baseUrl}/steam/import/${sessionId}/progress${tokenQs()}`;

  const eventSource = new EventSource(url);

  // Listen for all known event types from the backend
  const eventTypes = [
    'import_started', 'status', 'profile_validated', 'games_discovered',
    'game_start', 'game_complete', 'game_error',
    'screenshot_complete', 'screenshot_skipped', 'screenshot_failed',
    'import_complete', 'import_error', 'import_cancelled', 'done',
  ];

  for (const type of eventTypes) {
    eventSource.addEventListener(type, (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        onEvent(type, data);
      } catch {
        onEvent(type, e.data);
      }
    });
  }

  if (onError) {
    eventSource.onerror = onError;
  }

  return eventSource;
}

export async function cancelImport(sessionId: number): Promise<void> {
  return api.post(`/steam/import/${sessionId}/cancel`);
}
