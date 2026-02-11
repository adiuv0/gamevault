import { useEffect, useState } from 'react';
import { Gamepad2, Plus, Loader2 } from 'lucide-react';
import { EmptyState } from '@/components/shared/EmptyState';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { GameGrid } from '@/components/games/GameGrid';
import { GameList } from '@/components/games/GameList';
import { useViewStore } from '@/stores/viewStore';
import { listGames, createGame } from '@/api/games';
import type { Game } from '@/lib/types';

export function LibraryPage() {
  const { viewMode, sortBy } = useViewStore();
  const [games, setGames] = useState<Game[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newGameName, setNewGameName] = useState('');
  const [creating, setCreating] = useState(false);

  const fetchGames = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await listGames(sortBy);
      setGames(data.games);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load games');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchGames();
  }, [sortBy]);

  const handleCreateGame = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newGameName.trim()) return;

    try {
      setCreating(true);
      await createGame({ name: newGameName.trim() });
      setNewGameName('');
      setShowCreateModal(false);
      await fetchGames();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create game');
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <p className="text-accent-danger mb-4">{error}</p>
        <button
          onClick={fetchGames}
          className="px-4 py-2 bg-bg-tertiary text-text-primary rounded-md text-sm hover:bg-bg-tertiary/80 transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-text-primary">Library</h1>
          {games.length > 0 && (
            <span className="text-sm text-text-muted bg-bg-tertiary px-2 py-0.5 rounded-full">
              {games.length} {games.length === 1 ? 'game' : 'games'}
            </span>
          )}
        </div>

        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-2 px-3 py-1.5 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Add Game
        </button>
      </div>

      {/* Content */}
      {games.length === 0 ? (
        <EmptyState
          icon={<Gamepad2 className="h-12 w-12" />}
          title="No games yet"
          description="Upload screenshots or import from Steam to get started."
          action={
            <div className="flex gap-3">
              <a
                href="/upload"
                className="px-4 py-2 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors no-underline"
              >
                Upload Screenshots
              </a>
              <a
                href="/import/steam"
                className="px-4 py-2 bg-bg-tertiary text-text-primary rounded-md text-sm font-medium hover:bg-bg-tertiary/80 transition-colors border border-border no-underline"
              >
                Import from Steam
              </a>
            </div>
          }
        />
      ) : viewMode === 'grid' ? (
        <GameGrid games={games} />
      ) : (
        <GameList games={games} />
      )}

      {/* Create Game Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setShowCreateModal(false)}
          />
          <div className="relative bg-bg-secondary border border-border rounded-lg p-6 w-full max-w-md shadow-xl">
            <h2 className="text-lg font-semibold text-text-primary mb-4">Add Game</h2>
            <form onSubmit={handleCreateGame}>
              <input
                type="text"
                value={newGameName}
                onChange={(e) => setNewGameName(e.target.value)}
                placeholder="Game name..."
                className="w-full px-3 py-2 bg-bg-primary border border-border rounded-md text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary text-sm mb-4"
                autoFocus
              />
              <div className="flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="px-4 py-2 bg-bg-tertiary text-text-primary rounded-md text-sm hover:bg-bg-tertiary/80 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!newGameName.trim() || creating}
                  className="px-4 py-2 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  {creating && <Loader2 className="h-3 w-3 animate-spin" />}
                  Create
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
