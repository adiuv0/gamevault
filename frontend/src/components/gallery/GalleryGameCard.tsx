import { Link } from 'react-router-dom';
import { ImageOff } from 'lucide-react';
import { galleryCoverUrl } from '@/api/gallery';
import { formatCount, formatDate } from '@/lib/formatters';
import type { Game } from '@/lib/types';

interface GalleryGameCardProps {
  game: Game;
}

export function GalleryGameCard({ game }: GalleryGameCardProps) {
  return (
    <Link
      to={`/gallery/games/${game.id}`}
      className="group block rounded-lg overflow-hidden bg-bg-secondary border border-border hover:border-accent-primary/50 transition-all duration-200 hover:scale-[1.02] hover:shadow-lg hover:shadow-accent-primary/5 no-underline"
    >
      {/* Cover image */}
      <div className="relative aspect-[2/3] bg-bg-tertiary overflow-hidden">
        {game.cover_image_path ? (
          <img
            src={galleryCoverUrl(game.id)}
            alt={game.name}
            className="w-full h-full object-cover group-hover:brightness-110 transition-all duration-200"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <ImageOff className="h-12 w-12 text-text-muted" />
          </div>
        )}

        {/* Screenshot count badge */}
        <div className="absolute top-2 right-2 bg-black/70 backdrop-blur-sm text-white text-xs font-medium px-2 py-0.5 rounded-full">
          {game.screenshot_count}
        </div>

        {/* Hover overlay */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-200">
          <div className="absolute bottom-0 left-0 right-0 p-3">
            {game.last_screenshot_date && (
              <p className="text-xs text-gray-300">
                Last: {formatDate(game.last_screenshot_date)}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Game name */}
      <div className="p-3">
        <h3 className="text-sm font-medium text-text-primary truncate group-hover:text-accent-primary transition-colors">
          {game.name}
        </h3>
        <p className="text-xs text-text-muted mt-0.5">
          {formatCount(game.screenshot_count)}
        </p>
      </div>
    </Link>
  );
}
