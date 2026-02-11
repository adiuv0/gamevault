import { useEffect, useState } from 'react';
import { Gamepad2 } from 'lucide-react';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { EmptyState } from '@/components/shared/EmptyState';
import { GalleryGameCard } from '@/components/gallery/GalleryGameCard';
import { galleryListGames } from '@/api/gallery';
import type { Game } from '@/lib/types';

export function GalleryHomePage() {
  const [games, setGames] = useState<Game[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchGames = async () => {
      try {
        setLoading(true);
        const data = await galleryListGames('name');
        setGames(data.games);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load games');
      } finally {
        setLoading(false);
      }
    };
    fetchGames();
  }, []);

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
      </div>
    );
  }

  if (games.length === 0) {
    return (
      <EmptyState
        icon={<Gamepad2 className="h-12 w-12" />}
        title="No games available"
        description="There are no public games to display yet."
      />
    );
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Games</h1>
        <span className="text-sm text-text-muted bg-bg-tertiary px-2 py-0.5 rounded-full">
          {games.length} {games.length === 1 ? 'game' : 'games'}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7 gap-4">
        {games.map((game) => (
          <GalleryGameCard key={game.id} game={game} />
        ))}
      </div>
    </div>
  );
}
