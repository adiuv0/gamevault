import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { getCoverUrl } from '@/api/games';
import { formatCount, formatDate, isHexColor } from '@/lib/formatters';
import type { Game } from '@/lib/types';

interface GameGridCardProps {
  game: Game;
}

export function GameGridCard({ game }: GameGridCardProps) {
  const coverUrl = game.cover_image_path
    ? getCoverUrl(game.id, game.updated_at)
    : null;
  // Hovering a cover outlines it in that game's own colour.
  const style = isHexColor(game.accent_color)
    ? ({ '--color-accent-primary': game.accent_color } as CSSProperties)
    : undefined;

  return (
    <Link
      to={`/games/${game.id}`}
      style={style}
      title={game.last_screenshot_date ? `Last capture ${formatDate(game.last_screenshot_date)}` : undefined}
      className="group block no-underline"
    >
      {/* Cover image - 2:3 aspect ratio (Steam library format) */}
      <div className="relative aspect-[2/3] rounded bg-bg-tertiary overflow-hidden outline-2 outline-offset-2 outline-transparent group-hover:outline-accent-primary transition-[outline-color]">
        {coverUrl ? (
          <img
            src={coverUrl}
            alt={game.name}
            className="w-full h-full object-cover"
            loading="lazy"
            decoding="async"
          />
        ) : (
          // No cover art: the name, set like a title card.
          <div className="w-full h-full flex items-end p-3 bg-gradient-to-b from-bg-tertiary to-bg-secondary">
            <span className="display-title text-xl text-text-secondary line-clamp-4 break-words">{game.name}</span>
          </div>
        )}
      </div>

      <div className="pt-2">
        <h3 className="text-sm font-medium text-text-primary truncate group-hover:text-accent-primary transition-colors">
          {game.name}
        </h3>
        <p className="text-xs text-text-muted mt-0.5 tabular-nums">
          {formatCount(game.screenshot_count)}
        </p>
      </div>
    </Link>
  );
}
