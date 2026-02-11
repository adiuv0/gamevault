import { useEffect, useState } from 'react';
import { ChevronDown, Plus, Loader2 } from 'lucide-react';
import { listGames, createGame } from '@/api/games';
import type { Game } from '@/lib/types';

interface GameSelectorProps {
  selectedGameId: number | null;
  onSelect: (gameId: number) => void;
}

export function GameSelector({ selectedGameId, onSelect }: GameSelectorProps) {
  const [games, setGames] = useState<Game[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');

  useEffect(() => {
    loadGames();
  }, []);

  const loadGames = async () => {
    try {
      const data = await listGames('name');
      setGames(data.games);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const selectedGame = games.find((g) => g.id === selectedGameId);

  const filteredGames = games.filter((g) =>
    g.name.toLowerCase().includes(search.toLowerCase()),
  );

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      setCreating(true);
      const game = await createGame({ name: newName.trim() });
      setGames((prev) => [...prev, game].sort((a, b) => a.name.localeCompare(b.name)));
      onSelect(game.id);
      setNewName('');
      setOpen(false);
    } catch {
      // ignore
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-muted">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading games...
      </div>
    );
  }

  return (
    <div className="relative">
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-primary hover:border-accent-primary/50 transition-colors"
      >
        <span className={selectedGame ? 'text-text-primary' : 'text-text-muted'}>
          {selectedGame ? selectedGame.name : 'Select a game...'}
        </span>
        <ChevronDown className={`h-4 w-4 text-text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute top-full left-0 right-0 z-10 mt-1 bg-bg-secondary border border-border rounded-md shadow-lg max-h-64 overflow-hidden flex flex-col">
          {/* Search */}
          <div className="p-2 border-b border-border">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search games..."
              className="w-full px-2 py-1.5 bg-bg-primary border border-border rounded text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary"
              autoFocus
            />
          </div>

          {/* Game list */}
          <div className="overflow-y-auto max-h-40">
            {filteredGames.length === 0 ? (
              <div className="px-3 py-4 text-sm text-text-muted text-center">
                No games found
              </div>
            ) : (
              filteredGames.map((game) => (
                <button
                  key={game.id}
                  type="button"
                  onClick={() => {
                    onSelect(game.id);
                    setOpen(false);
                    setSearch('');
                  }}
                  className={`w-full text-left px-3 py-2 text-sm hover:bg-bg-tertiary transition-colors ${
                    game.id === selectedGameId
                      ? 'text-accent-primary bg-accent-primary/10'
                      : 'text-text-primary'
                  }`}
                >
                  {game.name}
                  <span className="text-text-muted ml-2 text-xs">
                    ({game.screenshot_count})
                  </span>
                </button>
              ))
            )}
          </div>

          {/* Create new game */}
          <div className="p-2 border-t border-border">
            <div className="flex gap-2">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="New game name..."
                className="flex-1 px-2 py-1.5 bg-bg-primary border border-border rounded text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleCreate();
                  }
                }}
              />
              <button
                type="button"
                onClick={handleCreate}
                disabled={!newName.trim() || creating}
                className="px-2 py-1.5 bg-accent-primary text-white rounded text-sm hover:bg-accent-primary/90 transition-colors disabled:opacity-50 flex items-center gap-1"
              >
                {creating ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Plus className="h-3 w-3" />
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
