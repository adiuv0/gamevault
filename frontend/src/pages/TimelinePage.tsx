import { useState, useEffect, useCallback, useRef, type CSSProperties } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Clock, Calendar, ChevronDown, Loader2, Heart, MessageSquare,
  Filter, BarChart3, ArrowUp,
} from 'lucide-react';
import { EmptyState } from '@/components/shared/EmptyState';
import { TimelineInsights } from '@/components/timeline/TimelineInsights';
import { getTimeline, getTimelineInsights } from '@/api/timeline';
import { getThumbnailUrl } from '@/api/screenshots';
import { listGames } from '@/api/games';
import { formatSessionLength, isHexColor } from '@/lib/formatters';
import type {
  TimelineDay, TimelineInsights as Insights, TimelineScreenshot, TimelineSession,
} from '@/api/timeline';

const STATS_KEY = 'gamevault.timeline.showStats';

function readShowStats(): boolean {
  try {
    return localStorage.getItem(STATS_KEY) !== 'false';
  } catch {
    return true;
  }
}

export function TimelinePage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // State
  const [days, setDays] = useState<TimelineDay[]>([]);
  const [insights, setInsights] = useState<Insights | null>(null);
  // A calendar year, or null for all time. Starts on this year and falls back to
  // all time when this year has no captures yet.
  const [year, setYear] = useState<number | null>(() => new Date().getFullYear());
  const [showStats, setShowStats] = useState(readShowStats);
  // Set by clicking a day on the calendar: the Timeline then starts at that day.
  const [jumpDate, setJumpDate] = useState<string | null>(searchParams.get('date'));
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

  // Load statistics for the chosen year and game.
  useEffect(() => {
    let cancelled = false;
    getTimelineInsights({ year, game_id: gameId })
      .then((result) => {
        if (cancelled) return;
        if (year !== null && result.years.length > 0 && !result.years.includes(year)) {
          setYear(null);
          return;
        }
        setInsights(result);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [year, gameId]);

  const toggleStats = () => {
    setShowStats((current) => {
      try {
        localStorage.setItem(STATS_KEY, String(!current));
      } catch {
        // Not remembered in private windows; the toggle still works.
      }
      return !current;
    });
  };

  // Changing the game filter while a page is in flight must not let the old
  // filter's response land last.
  const requestVersion = useRef(0);

  // Load timeline data
  const loadTimeline = useCallback(async (pageNum: number, append = false) => {
    const version = append ? requestVersion.current : ++requestVersion.current;
    if (pageNum === 1) setLoading(true);
    else setLoadingMore(true);

    try {
      const result = await getTimeline({
        game_id: gameId || undefined,
        end_date: jumpDate || undefined,
        page: pageNum,
        limit: 20,
      });
      if (version !== requestVersion.current) return;

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
      if (version === requestVersion.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [gameId, jumpDate]);

  useEffect(() => {
    loadTimeline(1);
  }, [loadTimeline]);

  // Update URL params
  useEffect(() => {
    const params = new URLSearchParams();
    if (gameId) params.set('game_id', String(gameId));
    if (jumpDate) params.set('date', jumpDate);
    setSearchParams(params, { replace: true });
  }, [gameId, jumpDate, setSearchParams]);

  const jumpTo = (date: string | null) => {
    setJumpDate(date);
    document.querySelector('main')?.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const openCapture = (capture: TimelineScreenshot) => navigate(`/games/${capture.game_id}`);

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

    if (daysDiff >= 0) {
      if (daysDiff === 0) return 'Today';
      if (daysDiff === 1) return 'Yesterday';
      if (daysDiff < 7) return `${daysDiff} days ago`;
    }

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
      <div className="flex flex-wrap items-end justify-between gap-3 mb-6">
        <div>
          <h1 className="display-title text-3xl sm:text-4xl text-text-primary">Timeline</h1>
          {insights && insights.total_captures > 0 && (
            <p className="text-sm text-text-muted mt-1">
              {insights.total_captures.toLocaleString()} captures across {insights.active_days.toLocaleString()} days
              {insights.range.start && insights.range.end && (
                <> from {formatDateShort(insights.range.start)} to {formatDateShort(insights.range.end)}</>
              )}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {insights && insights.years.length > 0 && (
            <div className="flex flex-wrap gap-1" role="group" aria-label="Statistics range">
              {[...insights.years.slice(0, 4), null].map((option) => (
                <button
                  key={option ?? 'all'}
                  type="button"
                  aria-pressed={year === option}
                  onClick={() => setYear(option)}
                  className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
                    year === option
                      ? 'border-accent-primary text-accent-primary'
                      : 'border-border text-text-secondary hover:text-text-primary'
                  }`}
                >
                  {option ?? 'All time'}
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={toggleStats}
            aria-pressed={showStats}
            title={showStats ? 'Hide statistics' : 'Show statistics'}
            className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded-md border transition-colors ${
              showStats
                ? 'border-border text-text-primary'
                : 'border-border text-text-secondary hover:text-text-primary'
            }`}
          >
            <BarChart3 className="h-3.5 w-3.5" />
            Stats
          </button>
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
              <span className="bg-accent-primary text-on-accent text-xs px-1.5 py-0.5 rounded-full">1</span>
            )}
          </button>
        </div>
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

      {showStats && insights && insights.total_captures > 0 && (
        <TimelineInsights
          insights={insights}
          selectedDate={jumpDate}
          onJump={jumpTo}
          onOpenCapture={openCapture}
        />
      )}

      {jumpDate && (
        <div className="mb-6 flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-bg-secondary px-4 py-2.5 text-sm">
          <span className="text-text-secondary">
            Showing {formatDateShort(jumpDate)} and earlier
          </span>
          <button
            type="button"
            onClick={() => jumpTo(null)}
            className="flex items-center gap-1.5 text-accent-primary hover:text-accent-primary/80"
          >
            <ArrowUp className="h-3.5 w-3.5" />
            Back to latest
          </button>
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
              onScreenshotClick={openCapture}
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

const formatClock = (moment: string) =>
  new Date(moment.replace(' ', 'T')).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

function TimelineDayCard({
  day,
  formatDate,
  onScreenshotClick,
}: {
  day: TimelineDay;
  formatDate: (d: string) => string;
  onScreenshotClick: (capture: TimelineScreenshot) => void;
}) {
  const byId = new Map(day.screenshots.map((s) => [s.id, s]));
  const summary = [
    `${day.screenshot_count.toLocaleString()} ${day.screenshot_count === 1 ? 'capture' : 'captures'}`,
    day.session_count > 1 ? `${day.session_count} sessions` : null,
    day.session_minutes > 0 ? formatSessionLength(day.session_minutes) : null,
  ].filter(Boolean).join(' · ');

  return (
    <section aria-label={formatDate(day.date)}>
      {/* Day header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex items-center gap-2">
          <Calendar className="h-4 w-4 text-accent-primary" />
          <h2 className="text-sm font-semibold text-text-primary">
            {formatDate(day.date)}
          </h2>
        </div>
        <span className="text-xs text-text-muted tabular-nums">{summary}</span>
        <div className="flex-1 h-px bg-border" />
      </div>

      <div className="space-y-5">
        {day.sessions.map((session) => (
          <TimelineSessionRow
            key={`${session.game_id}-${session.started_at}`}
            session={session}
            captures={session.screenshot_ids.map((id) => byId.get(id)).filter((s): s is TimelineScreenshot => !!s)}
            onScreenshotClick={onScreenshotClick}
          />
        ))}
      </div>
    </section>
  );
}

function TimelineSessionRow({
  session,
  captures,
  onScreenshotClick,
}: {
  session: TimelineSession;
  captures: TimelineScreenshot[];
  onScreenshotClick: (capture: TimelineScreenshot) => void;
}) {
  const style = isHexColor(session.accent_color)
    ? ({ '--color-accent-primary': session.accent_color } as CSSProperties)
    : undefined;
  const single = session.capture_count === 1;
  const hidden = session.capture_count - captures.length;

  return (
    <div style={style} className="grid grid-cols-[3px_minmax(0,1fr)] gap-3">
      <div className="rounded-full bg-accent-primary" aria-hidden />
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[13px] text-text-secondary tabular-nums">
          <button
            type="button"
            onClick={() => captures[0] && onScreenshotClick(captures[0])}
            className="font-display text-lg font-semibold uppercase tracking-wide text-text-primary hover:text-accent-primary transition-colors"
          >
            {session.game_name}
          </button>
          <span>
            {single
              ? formatClock(session.started_at)
              : `${formatClock(session.started_at)} – ${formatClock(session.ended_at)}`}
          </span>
          <span className="text-text-muted">
            {single
              ? '1 capture'
              : `${formatSessionLength(session.duration_minutes)} · ${session.capture_count.toLocaleString()} captures`}
            {session.hdr_count > 0 && ` · ${session.hdr_count.toLocaleString()} HDR`}
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-1">
          {captures.map((screenshot, index) => (
            <button
              key={screenshot.id}
              onClick={() => onScreenshotClick(screenshot)}
              className="group relative aspect-video rounded-sm overflow-hidden bg-bg-tertiary outline-2 -outline-offset-2 outline-transparent hover:outline-accent-primary transition-[outline-color]"
            >
              <img
                src={getThumbnailUrl(screenshot.id, 'sm', screenshot.media_version)}
                alt={screenshot.filename}
                loading="lazy"
                decoding="async"
                className="w-full h-full object-cover"
              />

              {/* Hover overlay */}
              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors" />

              {/* Badges */}
              <div className="absolute top-1 right-1 flex gap-1">
                {Boolean(screenshot.is_favorite) && (
                  <span className="bg-black/60 rounded-full p-0.5">
                    <Heart className="h-2.5 w-2.5 text-accent-danger fill-accent-danger" />
                  </span>
                )}
                {Boolean(screenshot.has_annotation) && (
                  <span className="bg-black/60 rounded-full p-0.5">
                    <MessageSquare className="h-2.5 w-2.5 text-accent-primary" />
                  </span>
                )}
              </div>

              {/* The last preview of a long session says how many more it holds. */}
              {hidden > 0 && index === captures.length - 1 && (
                <div className="absolute inset-0 flex items-center justify-center bg-black/60 text-sm font-semibold text-white">
                  +{hidden.toLocaleString()}
                </div>
              )}

              {/* Time on hover */}
              <div className="absolute bottom-0 left-0 right-0 p-1.5 bg-gradient-to-t from-black/70 to-transparent opacity-0 group-hover:opacity-100 transition-opacity">
                <p className="text-[10px] text-white truncate">
                  {formatClock(screenshot.taken_at ?? screenshot.uploaded_at)}
                </p>
              </div>
            </button>
          ))}

        </div>
      </div>
    </div>
  );
}
