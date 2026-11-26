import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { listScreenshotsCursor, getThumbnailUrl } from '@/api/screenshots';
import { getCoverUrl } from '@/api/games';
import { formatCount, isHexColor, timeAgo } from '@/lib/formatters';
import type { Game, ScreenshotListItem } from '@/lib/types';

const SHELF_SIZE = 3;

/** The games captured most recently, each shown behind its latest capture. */
export function RecentShelf({ games }: { games: Game[] }) {
  const recent = useMemo(
    () =>
      games
        .filter((g) => g.screenshot_count > 0 && g.last_screenshot_date)
        .sort((a, b) => (b.last_screenshot_date ?? '').localeCompare(a.last_screenshot_date ?? ''))
        .slice(0, SHELF_SIZE),
    [games],
  );
  const [latest, setLatest] = useState<Record<number, ScreenshotListItem>>({});
  // Refetch only when the set of games or their newest capture changes, not on every re-sort.
  const key = recent.map((g) => `${g.id}:${g.last_screenshot_date}`).join('|');

  useEffect(() => {
    let cancelled = false;
    for (const game of recent) {
      listScreenshotsCursor(game.id, null, 1)
        .then((page) => {
          const shot = page.screenshots[0];
          if (!cancelled && shot) setLatest((current) => ({ ...current, [game.id]: shot }));
        })
        .catch(() => {
          // The banner falls back to the cover art.
        });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` stands in for `recent`
  }, [key]);

  if (recent.length === 0) return null;

  return (
    <section className="mb-8" aria-label="Jump back in">
      <h2 className="shelf-label mb-3">Jump back in</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-[1.4fr_1fr_1fr] gap-3">
        {recent.map((game, index) => {
          const shot = latest[game.id];
          const image = shot
            ? getThumbnailUrl(shot.id, 'md', shot.media_version)
            : game.cover_image_path
              ? getCoverUrl(game.id, game.updated_at)
              : null;
          const style = isHexColor(game.accent_color)
            ? ({ '--color-accent-primary': game.accent_color } as CSSProperties)
            : undefined;
          return (
            <Link
              key={game.id}
              to={`/games/${game.id}`}
              style={style}
              className={`group relative block h-40 sm:h-48 overflow-hidden rounded-md bg-bg-tertiary no-underline outline-2 outline-offset-2 outline-transparent hover:outline-accent-primary transition-[outline-color] ${
                index === 2 ? 'sm:hidden lg:block' : ''
              }`}
            >
              {image && (
                <img
                  src={image}
                  alt=""
                  decoding="async"
                  className="absolute inset-0 h-full w-full object-cover"
                />
              )}
              <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/20 to-transparent" />
              <div className="absolute inset-x-0 bottom-0 p-4">
                <h3 className="display-title text-2xl sm:text-[28px] text-white line-clamp-2 group-hover:text-accent-primary transition-colors">
                  {game.name}
                </h3>
                <p className="mt-1.5 text-[13px] text-white/75">
                  {timeAgo(game.last_screenshot_date!)} · {formatCount(game.screenshot_count)}
                </p>
              </div>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
