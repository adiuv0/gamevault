import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Clock, Calendar, ChevronDown, Loader2, Heart, MessageSquare,
  ImageIcon, Filter,
} from 'lucide-react';
import { EmptyState } from '@/components/shared/EmptyState';
import { getTimeline, getTimelineStats } from '@/api/timeline';
import { getThumbnailUrl } from '@/api/screenshots';
import { listGames } from '@/api/games';
import type { TimelineDay, TimelineStats } from '@/api/timeline';

export function TimelinePage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // State
  const [days, setDays] = useState<TimelineDay[]>([]);
  const [stats, setStats] = useState<TimelineStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [totalDays, setTotalDays] = useState(0);

  // Filters
  const [gameId, setGameId] = useState<number | null>(
    searchParams.get('game_id') ? Number(searchParams.get('game_id')) : null
  );
  const [games, setGames] = useState<Array<{ id: number; name: string }>>([]);
  const [showFilters, setShowFilters] = useState(false);

  // Load games list for filter
  useEffect(() => {
    listGames().then(data => setGames(data.games.map(game => ({
      id: game.id,
      name: game.name,
    })))).catch(() => {});
  }, []);

  // Load timeline stats
  useEffect(() => {
    getTimelineStats().then(setStats).catch(() => {});
  }, []);

  // Load timeline data
  const loadTimeline = useCallback(async (pageNum: number, append = false) => {
    if (pageNum === 1) setLoading(true);
    else setLoadingMore(true);

    try {
      const result = await getTimeline({
        game_id: gameId || undefined,
        page: pageNum,
        limit: 20,
      });

      if (append) {
        setDays(prev => [...prev, ...result.days]);
      } else {
        setDays(result.days);
      }
      setHasMore(result.has_more);
      setTotalDays(result.total_days);
      setPage(pageNum);
    } catch {
      // ignore
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [gameId]);

  useEffect(() => {
    loadTimeline(1);
  }, [loadTimeline]);

  // Update URL params
  useEffect(() => {
    const params = new URLSearchParams();
    if (gameId) params.set('game_id', String(gameId));
    setSearchParams(params, { replace: true });
  }, [gameId, setSearchParams]);

  const handleLoadMore = () => {
    if (!loadingMore && hasMore) {
      loadTimeline(page + 1, true);
    }
  };

  const clearFilters = () => {
    setGameId(null);
  };

  const hasActiveFilters = gameId !== null;

  // ── Format helpers ──────────────────────────────────────────────────────

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr + 'T00:00:00');
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const diff = today.getTime() - date.getTime();
    const daysDiff = Math.floor(diff / (1000 * 60 * 60 * 24));

    if (daysDiff === 0) return 'Today';
    if (daysDiff === 1) return 'Yesterday';
    if (daysDiff < 7) return `${daysDiff} days ago`;

    return date.toLocaleDateString('en-US', {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  };

  const formatDateShort = (dateStr: string) => {
    const date = new Date(dateStr + 'T00:00:00');
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Timeline</h1>
          {stats && stats.total_screenshots > 0 && (
            <p className="text-sm text-text-muted mt-1">
              {stats.total_screenshots.toLocaleString()} screenshots across {stats.total_days} days
              {stats.earliest_date && stats.latest_date && (
                <> from {formatDateShort(stats.earliest_date)} to {formatDateShort(stats.latest_date)}</>
              )}
            </p>
          )}
        </div>

        <button
          onClick={() => setShowFilters(!showFilters)}
          className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded-md border transition-colors ${
            hasActiveFilters
              ? 'bg-accent-primary/10 border-accent-primary/30 text-accent-primary'
              : 'border-border text-text-secondary hover:text-text-primary'
          }`}
        >
          <Filter className="h-3.5 w-3.5" />
          Filter
          {hasActiveFilters && (
            <span className="bg-accent-primary text-white text-xs px-1.5 py-0.5 rounded-full">1</span>
          )}
        </button>
      </div>

      {/* Filters panel */}
      {showFilters && (
        <div className="mb-6 bg-bg-secondary border border-border rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-text-primary">Filters</h3>
            {hasActiveFilters && (
              <button
                onClick={clearFilters}
                className="text-xs text-accent-primary hover:text-accent-primary/80"
              >
                Clear all
              </button>
            )}
          </div>

          <div>
            <label className="block text-xs text-text-muted mb-1">Game</label>
            <select
              value={gameId || ''}
              onChange={e => setGameId(e.target.value ? Number(e.target.value) : null)}
              className="w-full max-w-xs px-3 py-1.5 bg-bg-primary border border-border rounded-md text-sm text-text-primary focus:outline-none focus:border-accent-primary"
            >
              <option value="">All games</option>
              {games.map(g => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-accent-primary" />
        </div>
      )}

      {/* Empty state */}
      {!loading && days.length === 0 && (
        <EmptyState
          icon={<Clock className="h-12 w-12" />}
          title={hasActiveFilters ? 'No screenshots match filters' : 'No screenshots yet'}
          description={
            hasActiveFilters
              ? 'Try adjusting your filters to see more results.'
              : 'Your screenshot timeline will appear here once you upload or import screenshots.'
          }
        />
      )}

      {/* Timeline */}
      {!loading && days.length > 0 && (
        <div className="space-y-8">
          {days.map(day => (
            <TimelineDayCard
              key={day.date}
              day={day}
              formatDate={formatDate}
              onScreenshotClick={(_screenshotId, gameIdClicked) => {
                navigate(`/games/${gameIdClicked}`);
              }}
            />
          ))}

          {/* Load more */}
          {hasMore && (
            <div className="flex justify-center pt-4">
              <button
                onClick={handleLoadMore}
                disabled={loadingMore}
                className="flex items-center gap-2 px-4 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-secondary hover:text-text-primary hover:border-border transition-colors disabled:opacity-50"
              >
                {loadingMore ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
                Load more ({totalDays - days.length} more days)
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Day Card Component ──────────────────────────────────────────────────────

function TimelineDayCard({
  day,
  formatDate,
  onScreenshotClick,
}: {
  day: TimelineDay;
  formatDate: (d: string) => string;
  onScreenshotClick: (screenshotId: number, gameId: number) => void;
}) {
  return (
    <div>
      {/* Day header */}
      <div className="flex items-center gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Calendar className="h-4 w-4 text-accent-primary" />
          <h2 className="text-sm font-semibold text-text-primary">
            {formatDate(day.date)}
          </h2>
        </div>
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <span className="flex items-center gap-1">
            <ImageIcon className="h-3 w-3" />
            {day.screenshot_count}
          </span>
          {day.games.length > 0 && (
            <span>
              {day.games.length === 1
                ? day.games[0]
                : `${day.games.length} games`}
            </span>
          )}
        </div>
        <div className="flex-1 h-px bg-border" />
      </div>

      {/* Screenshot grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-2">
        {day.screenshots.map(screenshot => (
          <button
            key={screenshot.id}
            onClick={() => onScreenshotClick(screenshot.id, screenshot.game_id)}
            className="group relative aspect-video rounded-md overflow-hidden bg-bg-tertiary hover:ring-2 hover:ring-accent-primary transition-all"
          >
            <img
              src={getThumbnailUrl(screenshot.id, 'sm')}
              alt={screenshot.filename}
              loading="lazy"
              className="w-full h-full object-cover"
            />

            {/* Hover overlay */}
            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors" />

            {/* Badges */}
            <div className="absolute top-1 right-1 flex gap-1">
              {screenshot.is_favorite && (
                <span className="bg-black/60 rounded-full p-0.5">
                  <Heart className="h-2.5 w-2.5 text-accent-danger fill-accent-danger" />
                </span>
              )}
              {screenshot.has_annotation && (
                <span className="bg-black/60 rounded-full p-0.5">
                  <MessageSquare className="h-2.5 w-2.5 text-accent-primary" />
                </span>
              )}
            </div>

            {/* Game name on hover */}
            <div className="absolute bottom-0 left-0 right-0 p-1.5 bg-gradient-to-t from-black/70 to-transparent opacity-0 group-hover:opacity-100 transition-opacity">
              <p className="text-[10px] text-white truncate">{screenshot.game_name}</p>
            </div>
          </button>
        ))}

        {/* "More" indicator if day has more than shown */}
        {day.screenshot_count > day.screenshots.length && (
          <div className="aspect-video rounded-md bg-bg-tertiary flex items-center justify-center">
            <p className="text-xs text-text-muted">
              +{day.screenshot_count - day.screenshots.length} more
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
