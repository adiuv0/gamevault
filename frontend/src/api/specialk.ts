import { api, tokenQs } from './client';

export interface SpecialKScanGame {
  folder_name: string;
  suggested_name: string;
  screenshot_count: number;
  has_hdr: boolean;
  has_sdr: boolean;
}

export interface SpecialKScanResponse {
  valid: boolean;
  path: string;
  total_games: number;
  total_screenshots: number;
  games: SpecialKScanGame[];
  error: string | null;
}

export async function scanSpecialK(path: string): Promise<SpecialKScanResponse> {
  return api.post('/specialk/scan', { path });
}

export async function startSpecialKImport(data: {
  path: string;
  folder_names?: string[];
}): Promise<{ session_id: number }> {
  return api.post('/specialk/import', data);
}

export function connectSpecialKProgress(
  sessionId: number,
  onEvent: (event: string, data: unknown) => void,
  onError?: (e: Event) => void,
): EventSource {
  const url = `/api/specialk/import/${sessionId}/progress${tokenQs()}`;
  const eventSource = new EventSource(url);

  const eventTypes = [
    'import_started', 'status', 'games_discovered',
    'game_start', 'game_complete',
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

export async function cancelSpecialKImport(sessionId: number): Promise<void> {
  return api.post(`/specialk/import/${sessionId}/cancel`);
}
