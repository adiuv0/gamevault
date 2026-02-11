import { api } from './client';

export async function uploadScreenshots(
  files: File[],
  gameId: number,
  onProgress?: (loaded: number, total: number) => void,
): Promise<{ task_id: string; file_count: number }> {
  return api.uploadFiles('/upload', files, { game_id: String(gameId) }, onProgress);
}

export function connectUploadProgress(
  taskId: string,
  onEvent: (data: unknown) => void,
  onError?: (e: Event) => void,
): EventSource {
  return api.connectSSE(`/upload/progress/${taskId}`, onEvent, onError);
}
