import { useEffect, useMemo, useRef, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { Flame } from 'lucide-react';
import { getThumbnailUrl } from '@/api/screenshots';
import type { TimelineInsights as Insights, TimelineScreenshot } from '@/api/timeline';
import { formatSessionLength, isHexColor } from '@/lib/formatters';

const shortDate = (iso: string, withYear = false) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(withYear ? { year: 'numeric' } : {}),
  });

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

function accentStyle(color: string | null | undefined): CSSProperties | undefined {
  return isHexColor(color) ? ({ '--color-accent-primary': color } as CSSProperties) : undefined;
}

export function TimelineInsights({
  insights,
  selectedDate,
  onJump,
  onOpenCapture,
}: {
  insights: Insights;
  selectedDate: string | null;
  onJump: (date: string) => void;
  onOpenCapture: (capture: TimelineScreenshot) => void;
}) {
  return (
    <section aria-label="Statistics" className="mb-10 space-y-6">
      <StatStrip insights={insights} onJump={onJump} />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <CaptureCalendar insights={insights} selectedDate={selectedDate} onJump={onJump} />
        <TopGames insights={insights} />
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        {insights.on_this_day.captures.length > 0 ? (
          <OnThisDay insights={insights} onOpenCapture={onOpenCapture} />
        ) : (
          <div className="hidden lg:block" />
        )}
        <CaptureHours hours={insights.hours} />
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  detail,
  onClick,
  highlight,
}: {
  label: string;
  value: string;
  detail?: string;
  onClick?: () => void;
  highlight?: boolean;
}) {
  const body = (
    <>
      <span className="text-xs text-text-muted">{label}</span>
      <span
        className={`flex items-center gap-1.5 font-display text-[28px] font-bold leading-none tabular-nums ${
          highlight ? 'text-accent-warning' : 'text-text-primary'
        }`}
      >
        {highlight && <Flame className="h-5 w-5" aria-hidden />}
        {value}
      </span>
      {detail && <span className="truncate text-xs text-text-secondary">{detail}</span>}
    </>
  );
  const className = 'flex min-w-0 flex-col gap-1 bg-bg-secondary px-4 py-3 text-left';
  return onClick ? (
    <button type="button" onClick={onClick} className={`${className} hover:bg-bg-tertiary transition-colors`}>
      {body}
    </button>
  ) : (
    <div className={className}>{body}</div>
  );
}

function StatStrip({ insights, onJump }: { insights: Insights; onJump: (date: string) => void }) {
  const {
    total_captures, active_days, games, hdr_captures, busiest_day, best_streak,
    current_streak, session_count, longest_session, year,
  } = insights;
  const hdrShare = total_captures ? Math.round((hdr_captures / total_captures) * 100) : 0;
  const bestDetail = best_streak && best_streak.days > 1 && best_streak.start
    ? `best: ${plural(best_streak.days, 'day')} from ${shortDate(best_streak.start, year === null)}`
    : 'no streak yet';
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-3 xl:grid-cols-6">
      <Stat label="Captures" value={total_captures.toLocaleString()} detail={plural(games, 'game')} />
      <Stat
        label="Days with captures"
        value={active_days.toLocaleString()}
        detail={year ? `in ${year}` : 'all time'}
      />
      <Stat
        label="Current streak"
        value={plural(current_streak.days, 'day')}
        detail={bestDetail}
        highlight={current_streak.days > 1}
      />
      <Stat
        label="Busiest day"
        value={busiest_day ? busiest_day.captures.toLocaleString() : '–'}
        detail={busiest_day ? `${shortDate(busiest_day.date, year === null)} · ${busiest_day.game_name ?? ''}` : undefined}
        onClick={busiest_day ? () => onJump(busiest_day.date) : undefined}
      />
      <Stat
        label="Play sessions"
        value={session_count.toLocaleString()}
        detail={longest_session && longest_session.duration_minutes > 0
          ? `longest ${formatSessionLength(longest_session.duration_minutes)} · ${longest_session.game_name}`
          : undefined}
        onClick={longest_session ? () => onJump(longest_session.date) : undefined}
      />
      <Stat
        label="HDR share"
        value={`${hdrShare}%`}
        detail={`${hdr_captures.toLocaleString()} with an HDR file`}
      />
    </div>
  );
}

const CELL = 12;
const GAP = 3;
const LEVEL_CLASSES = [
  'bg-bg-tertiary',
  'bg-accent-primary/25',
  'bg-accent-primary/50',
  'bg-accent-primary/75',
  'bg-accent-primary',
];

function CaptureCalendar({
  insights,
  selectedDate,
  onJump,
}: {
  insights: Insights;
  selectedDate: string | null;
  onJump: (date: string) => void;
}) {
  const { weeks, months, thresholds } = useMemo(() => {
    const counts = new Map(insights.heatmap.days);
    const start = new Date(`${insights.heatmap.start}T00:00:00`);
    const end = new Date(`${insights.heatmap.end}T00:00:00`);
    // Columns are weeks starting on Sunday, like a wall calendar turned sideways.
    const cursor = new Date(start);
    cursor.setDate(cursor.getDate() - cursor.getDay());
    const weeks: Array<Array<{ date: string; count: number } | null>> = [];
    const months: Array<{ label: string; column: number }> = [];
    while (cursor <= end) {
      const week: Array<{ date: string; count: number } | null> = [];
      for (let d = 0; d < 7; d++) {
        if (cursor >= start && cursor <= end) {
          const iso = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}-${String(cursor.getDate()).padStart(2, '0')}`;
          week.push({ date: iso, count: counts.get(iso) ?? 0 });
          const firstCell = weeks.length === 0 && week.length === 1;
          if (cursor.getDate() === 1 || firstCell) {
            months.push({ label: cursor.toLocaleDateString('en-US', { month: 'short' }), column: weeks.length });
          }
        } else {
          week.push(null);
        }
        cursor.setDate(cursor.getDate() + 1);
      }
      weeks.push(week);
    }
    // A partial first month gets no label when the next one starts right after it.
    if (months.length > 1 && months[1].column - months[0].column < 3) months.shift();
    // Shade by quartile of the busy days, so one huge day doesn't wash out the rest.
    const busy = insights.heatmap.days.map(([, n]) => n).filter((n) => n > 0).sort((a, b) => a - b);
    const at = (q: number) => busy[Math.min(busy.length - 1, Math.floor(q * busy.length))] ?? 0;
    return { weeks, months, thresholds: [at(0.25), at(0.5), at(0.75)] };
  }, [insights.heatmap]);

  const level = (n: number) => {
    if (n <= 0) return 0;
    if (n <= thresholds[0]) return 1;
    if (n <= thresholds[1]) return 2;
    if (n <= thresholds[2]) return 3;
    return 4;
  };
  const width = weeks.length * (CELL + GAP);
  // On a narrow screen, open on the most recent weeks rather than January.
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [weeks]);

  return (
    <div className="min-w-0">
      <h2 className="shelf-label mb-3">
        Capture calendar{insights.year ? ` · ${insights.year}` : ' · last 12 months'}
      </h2>
      <div ref={scroller} className="overflow-x-auto pb-1">
        <div style={{ width }} className="relative">
          <div className="relative h-4 text-[11px] text-text-muted">
            {months.map((m) => (
              <span key={`${m.label}-${m.column}`} className="absolute" style={{ left: m.column * (CELL + GAP) }}>
                {m.label}
              </span>
            ))}
          </div>
          <div
            className="grid grid-flow-col"
            style={{ gridTemplateRows: `repeat(7, ${CELL}px)`, gap: GAP }}
            role="grid"
            aria-label="Captures per day"
          >
            {weeks.flatMap((week, w) =>
              week.map((day, d) =>
                day ? (
                  <button
                    key={day.date}
                    type="button"
                    disabled={day.count === 0}
                    onClick={() => onJump(day.date)}
                    title={`${shortDate(day.date, true)}: ${day.count ? plural(day.count, 'capture') : 'no captures'}`}
                    aria-label={`${shortDate(day.date, true)}, ${plural(day.count, 'capture')}`}
                    className={`rounded-[2px] ${LEVEL_CLASSES[level(day.count)]} ${
                      day.date === selectedDate ? 'outline-2 outline-offset-1 outline-text-primary' : ''
                    } enabled:hover:outline-2 enabled:hover:outline-text-secondary enabled:cursor-pointer`}
                    style={{ width: CELL, height: CELL }}
                  />
                ) : (
                  <span key={`pad-${w}-${d}`} style={{ width: CELL, height: CELL }} />
                ),
              ),
            )}
          </div>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-1 text-[11px] text-text-muted">
        <span className="mr-1">Less</span>
        {LEVEL_CLASSES.map((cls) => (
          <span key={cls} className={`inline-block rounded-[2px] ${cls}`} style={{ width: CELL - 1, height: CELL - 1 }} />
        ))}
        <span className="ml-1">More · click a day to jump to it</span>
      </div>
    </div>
  );
}

function TopGames({ insights }: { insights: Insights }) {
  const { top_games, other_games } = insights;
  const max = Math.max(1, top_games[0]?.captures ?? 0, other_games.captures);
  return (
    <div className="min-w-0">
      <h2 className="shelf-label mb-3">Most captured</h2>
      <ol className="space-y-2.5">
        {top_games.map((game) => (
          <li key={game.game_id} style={accentStyle(game.accent_color)}>
            <Link to={`/games/${game.game_id}`} className="group block no-underline">
              <div className="flex justify-between gap-3 text-sm">
                <span className="truncate text-text-primary group-hover:text-accent-primary transition-colors">
                  {game.game_name}
                </span>
                <span className="tabular-nums text-text-muted">{game.captures.toLocaleString()}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg-tertiary">
                <div className="h-full rounded-full bg-accent-primary" style={{ width: `${(game.captures / max) * 100}%` }} />
              </div>
            </Link>
          </li>
        ))}
        {other_games.games > 0 && (
          <li>
            <div className="flex justify-between gap-3 text-sm">
              <span className="text-text-secondary">{plural(other_games.games, 'other game')}</span>
              <span className="tabular-nums text-text-muted">{other_games.captures.toLocaleString()}</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg-tertiary">
              <div className="h-full rounded-full bg-text-muted" style={{ width: `${(other_games.captures / max) * 100}%` }} />
            </div>
          </li>
        )}
      </ol>
    </div>
  );
}

function OnThisDay({
  insights,
  onOpenCapture,
}: {
  insights: Insights;
  onOpenCapture: (capture: TimelineScreenshot) => void;
}) {
  const today = new Date(`${insights.on_this_day.date}T00:00:00`);
  const thisYear = today.getFullYear();
  return (
    <div>
      <h2 className="shelf-label mb-3">On this day · {shortDate(insights.on_this_day.date)}</h2>
      <div className="grid grid-cols-3 gap-1 sm:grid-cols-4 xl:grid-cols-8">
        {insights.on_this_day.captures.map((capture) => {
          const year = Number((capture.taken_at ?? capture.uploaded_at).slice(0, 4));
          const ago = thisYear - year;
          return (
            <button
              key={capture.id}
              type="button"
              onClick={() => onOpenCapture(capture)}
              className="group relative aspect-video overflow-hidden rounded-sm bg-bg-tertiary outline-2 -outline-offset-2 outline-transparent hover:outline-accent-primary transition-[outline-color]"
            >
              <img
                src={getThumbnailUrl(capture.id, 'sm', capture.media_version)}
                alt={capture.filename}
                loading="lazy"
                decoding="async"
                className="h-full w-full object-cover"
              />
              <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent p-1.5 text-left">
                <p className="text-[11px] font-semibold text-white">
                  {ago === 1 ? '1 year ago' : `${ago} years ago`}
                </p>
                <p className="truncate text-[10px] text-white/75">{capture.game_name}</p>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

const hourLabel = (hour: number) =>
  hour === 0 ? '12 AM' : hour < 12 ? `${hour} AM` : hour === 12 ? '12 PM' : `${hour - 12} PM`;

function CaptureHours({ hours }: { hours: number[] }) {
  const total = hours.reduce((a, b) => a + b, 0);
  if (!total) return null;
  const max = Math.max(...hours);
  const peak = hours.indexOf(max);
  // Evening into the small hours: 9 PM to 4 AM.
  const late = [21, 22, 23, 0, 1, 2, 3].reduce((sum, h) => sum + hours[h], 0) / total;
  const early = [5, 6, 7, 8, 9, 10, 11].reduce((sum, h) => sum + hours[h], 0) / total;
  const verdict = late >= 0.5
    ? `Night owl: ${Math.round(late * 100)}% of captures after 9 PM.`
    : early >= 0.4
      ? `Early bird: ${Math.round(early * 100)}% of captures before noon.`
      : `Most captures around ${hourLabel(peak)}.`;
  return (
    <div className="min-w-0">
      <h2 className="shelf-label mb-3">When you capture</h2>
      <div className="flex h-20 items-end gap-[2px]" role="img" aria-label={`Captures by hour of day. ${verdict}`}>
        {hours.map((count, hour) => (
          <div
            key={hour}
            title={`${hourLabel(hour)}: ${plural(count, 'capture')}`}
            className={`flex-1 rounded-t-[2px] ${hour === peak ? 'bg-accent-primary' : 'bg-accent-primary/45'}`}
            style={{ height: `${Math.max(count ? 4 : 1, (count / max) * 100)}%` }}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-text-muted">
        <span>12 AM</span><span>6 AM</span><span>12 PM</span><span>6 PM</span><span>11 PM</span>
      </div>
      <p className="mt-2 text-sm text-text-secondary">{verdict}</p>
      <p className="mt-0.5 text-xs text-text-muted">Times come from each capture&apos;s own clock.</p>
    </div>
  );
}
