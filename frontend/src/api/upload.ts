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

export interface UploadJobFile {
  file_index: number;
  display_name: string;
  size_bytes: number;
  status: 'pending' | 'processing' | 'completed' | 'skipped' | 'failed';
  screenshot_id: number | null;
  error_message: string | null;
}

export interface UploadJobSnapshot {
  type: 'snapshot';
  task_id: string;
  game_id: number;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  total_files: number;
  completed_files: number;
  skipped_files: number;
  failed_files: number;
  error: string | null;
  files: UploadJobFile[];
}

export async function getUploadJob(taskId: string): Promise<UploadJobSnapshot> {
  return api.get(`/upload/jobs/${taskId}`);
}
