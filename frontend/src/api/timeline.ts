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
  media_version?: string;
}

export interface TimelineSession {
  game_id: number;
  game_name: string;
  accent_color: string | null;
  started_at: string;
  ended_at: string;
  duration_minutes: number;
  capture_count: number;
  hdr_count: number;
  screenshot_ids: number[];
}

export interface TimelineDay {
  date: string;
  screenshot_count: number;
  games: string[];
  session_count: number;
  session_minutes: number;
  sessions: TimelineSession[];
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

export interface Streak {
  days: number;
  start: string | null;
  end: string | null;
}

export interface TimelineInsights {
  year: number | null;
  years: number[];
  range: { start: string | null; end: string | null };
  total_captures: number;
  active_days: number;
  games: number;
  hdr_captures: number;
  busiest_day: { date: string; captures: number; game_id: number | null; game_name: string | null } | null;
  best_streak: Streak | null;
  current_streak: Streak;
  session_count: number;
  longest_session: {
    game_id: number;
    game_name: string;
    date: string;
    duration_minutes: number;
    captures: number;
  } | null;
  top_games: Array<{ game_id: number; game_name: string; accent_color: string | null; captures: number }>;
  other_games: { games: number; captures: number };
  /** Captures per hour of day, 0-23, as each capture's own clock wrote it. */
  hours: number[];
  heatmap: { start: string; end: string; days: Array<[string, number]> };
  on_this_day: { date: string; captures: TimelineScreenshot[] };
}

/** The viewer's local calendar date as YYYY-MM-DD. */
export function localToday(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export async function getTimelineInsights(params: {
  year?: number | null;
  game_id?: number | null;
}): Promise<TimelineInsights> {
  const searchParams = new URLSearchParams({ today: localToday() });
  if (params.year) searchParams.set('year', String(params.year));
  if (params.game_id) searchParams.set('game_id', String(params.game_id));
  return api.get(`/timeline/insights?${searchParams.toString()}`);
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
