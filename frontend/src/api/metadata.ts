import { api } from './client';

export interface MetadataFetchResult {
  game_id: number;
  sources_tried: string[];
  sources_succeeded: string[];
  fields_updated: string[];
  cover_downloaded: boolean;
}

export interface ExternalGameResult {
  name: string;
  steam_app_id: number | null;
  cover_url: string | null;
  source: string;
}

export async function fetchGameMetadata(gameId: number): Promise<MetadataFetchResult> {
  return api.post(`/metadata/fetch/${gameId}`);
}

export async function searchExternalGames(query: string): Promise<{ results: ExternalGameResult[] }> {
  return api.get(`/metadata/search?q=${encodeURIComponent(query)}`);
}
