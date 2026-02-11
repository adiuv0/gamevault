import { api } from './client';
import type { Game } from '@/lib/types';

export async function listGames(sort = 'name'): Promise<{ games: Game[]; total: number }> {
  return api.get(`/games?sort=${sort}`);
}

export async function getGame(id: number): Promise<Game> {
  return api.get(`/games/${id}`);
}

export async function createGame(data: { name: string; steam_app_id?: number }): Promise<Game> {
  return api.post('/games', data);
}

export async function updateGame(id: number, data: Partial<Game>): Promise<Game> {
  return api.put(`/games/${id}`, data);
}

export async function deleteGame(id: number): Promise<void> {
  return api.delete(`/games/${id}`);
}

export async function refreshGameMetadata(id: number): Promise<Game> {
  return api.post(`/games/${id}/refresh-metadata`);
}
