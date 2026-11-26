import { GameGridCard } from './GameGridCard';
import type { Game } from '@/lib/types';

interface GameGridProps {
  games: Game[];
}

export function GameGrid({ games }: GameGridProps) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-x-4 gap-y-5">
      {games.map((game) => (
        <GameGridCard key={game.id} game={game} />
      ))}
    </div>
  );
}
