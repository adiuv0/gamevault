import { Link } from 'react-router-dom';
import { ImageOff, ChevronRight } from 'lucide-react';
import { getCoverUrl } from '@/api/games';
import { formatCount, formatDate } from '@/lib/formatters';
import type { Game } from '@/lib/types';

interface GameListRowProps {
  game: Game;
}

export function GameListRow({ game }: GameListRowProps) {
  const coverUrl = game.cover_image_path
    ? getCoverUrl(game.id)
    : null;

  return (
    <Link
      to={`/games/${game.id}`}
      className="flex items-center gap-4 px-4 py-3 bg-bg-secondary border border-border rounded-lg hover:border-accent-primary/50 hover:bg-bg-tertiary/30 transition-all duration-150 no-underline group"
    >
      {/* Mini cover */}
      <div className="w-10 h-14 rounded overflow-hidden bg-bg-tertiary flex-shrink-0">
        {coverUrl ? (
          <img src={coverUrl} alt="" className="w-full h-full object-cover" loading="lazy" />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <ImageOff className="h-4 w-4 text-text-muted" />
          </div>
        )}
      </div>

      {/* Name */}
      <div className="flex-1 min-w-0">
        <h3 className="text-sm font-medium text-text-primary truncate group-hover:text-accent-primary transition-colors">
          {game.name}
        </h3>
        {game.developer && (
          <p className="text-xs text-text-muted truncate">{game.developer}</p>
        )}
      </div>

      {/* Screenshots count */}
      <div className="text-right flex-shrink-0 w-32">
        <p className="text-sm text-text-secondary">{formatCount(game.screenshot_count)}</p>
      </div>

      {/* Last screenshot date */}
      <div className="text-right flex-shrink-0 w-32 hidden md:block">
        <p className="text-xs text-text-muted">
          {game.last_screenshot_date ? formatDate(game.last_screenshot_date) : '—'}
        </p>
      </div>

      <ChevronRight className="h-4 w-4 text-text-muted flex-shrink-0 group-hover:text-text-secondary" />
    </Link>
  );
}
