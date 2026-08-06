import { api } from './client';

export type DuplicateStatus = 'pending' | 'grouped' | 'dismissed';

export interface DuplicateScanJob {
  id: number;
  kind: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  cursor_id: number;
  total: number;
  processed: number;
  errors: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface DuplicateCandidate {
  id: number;
  game_id: number;
  game_name: string;
  left_screenshot_id: number;
  right_screenshot_id: number;
  left_filename: string;
  right_filename: string;
  left_format: string | null;
  right_format: string | null;
  left_width: number | null;
  left_height: number | null;
  right_width: number | null;
  right_height: number | null;
  left_taken_at: string | null;
  right_taken_at: string | null;
  hash_distance: number;
  confidence: 'likely' | 'possible';
  reason: string;
  status: DuplicateStatus;
}

export interface DuplicateCandidatePage {
  candidates: DuplicateCandidate[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

export async function getDuplicateScan(): Promise<DuplicateScanJob | null> {
  return api.get('/duplicates/scan');
}

export async function startDuplicateScan(): Promise<DuplicateScanJob> {
  return api.post('/duplicates/scan');
}

export async function listDuplicateCandidates(
  status: DuplicateStatus,
  page = 1,
): Promise<DuplicateCandidatePage> {
  return api.get(`/duplicates/candidates?status=${status}&page=${page}&limit=25`);
}

export async function groupDuplicateCandidate(id: number): Promise<void> {
  return api.post(`/duplicates/candidates/${id}/group`);
}

export async function dismissDuplicateCandidate(id: number): Promise<void> {
  return api.post(`/duplicates/candidates/${id}/dismiss`);
}
