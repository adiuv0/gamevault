import { api } from './client';

export interface SearchResult {
  screenshot_id: number;
  game_id: number;
  game_name: string;
  filename: string;
  file_path: string;
  thumbnail_path_sm: string | null;
  thumbnail_path_md: string | null;
  taken_at: string | null;
  uploaded_at: string;
  is_favorite: boolean | number;
  width: number | null;
  height: number | null;
  file_size: number | null;
  has_annotation: boolean | number;
  annotation_preview: string | null;
  relevance_score: number | null;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

export async function search(params: {
  q: string;
  game_id?: number;
  date_from?: string;
  date_to?: string;
  favorites_only?: boolean;
  sort?: string;
  page?: number;
  limit?: number;
}): Promise<SearchResponse> {
  const searchParams = new URLSearchParams();
  searchParams.set('q', params.q);
  if (params.game_id) searchParams.set('game_id', String(params.game_id));
  if (params.date_from) searchParams.set('date_from', params.date_from);
  if (params.date_to) searchParams.set('date_to', params.date_to);
  if (params.favorites_only) searchParams.set('favorites_only', 'true');
  if (params.sort) searchParams.set('sort', params.sort);
  if (params.page) searchParams.set('page', String(params.page));
  if (params.limit) searchParams.set('limit', String(params.limit));
  return api.get(`/search?${searchParams.toString()}`);
}
