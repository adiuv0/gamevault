import { api } from './client';

export interface TimelineScreenshot {
  id: number;
  filename: string;
  file_path: string;
  thumbnail_path_sm: string | null;
  thumbnail_path_md: string | null;
  taken_at: string | null;
  uploaded_at: string;
  is_favorite: boolean;
  width: number | null;
  height: number | null;
  game_id: number;
  game_name: string;
  has_annotation: boolean;
}

export interface TimelineDay {
  date: string;
  screenshot_count: number;
  games: string[];
  screenshots: TimelineScreenshot[];
}

export interface TimelineResponse {
  days: TimelineDay[];
  total_days: number;
  page: number;
  has_more: boolean;
}

export interface TimelineStats {
  total_screenshots: number;
  total_days: number;
  earliest_date: string | null;
  latest_date: string | null;
  total_games: number;
}

export async function getTimeline(params?: {
  start_date?: string;
  end_date?: string;
  game_id?: number;
  page?: number;
  limit?: number;
}): Promise<TimelineResponse> {
  const searchParams = new URLSearchParams();
  if (params?.start_date) searchParams.set('start_date', params.start_date);
  if (params?.end_date) searchParams.set('end_date', params.end_date);
  if (params?.game_id) searchParams.set('game_id', String(params.game_id));
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.limit) searchParams.set('limit', String(params.limit));
  const qs = searchParams.toString();
  return api.get(`/timeline${qs ? `?${qs}` : ''}`);
}

export async function getTimelineStats(): Promise<TimelineStats> {
  return api.get('/timeline/stats');
}
