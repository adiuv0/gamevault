import { GameListRow } from './GameListRow';
import type { Game } from '@/lib/types';

interface GameListProps {
  games: Game[];
}

export function GameList({ games }: GameListProps) {
  return (
    <div className="space-y-2">
      {/* Header */}
      <div className="flex items-center gap-4 px-4 py-2 text-xs font-medium text-text-muted uppercase tracking-wider">
        <div className="w-10 flex-shrink-0" />
        <div className="flex-1">Name</div>
        <div className="w-32 text-right">Screenshots</div>
        <div className="w-32 text-right hidden md:block">Last Taken</div>
        <div className="w-4 flex-shrink-0" />
      </div>

      {/* Rows */}
      {games.map((game) => (
        <GameListRow key={game.id} game={game} />
      ))}
    </div>
  );
}
