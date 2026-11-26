import { memo } from 'react';
import { Check, Files, Heart, MessageSquare } from 'lucide-react';
import { getThumbnailUrl } from '@/api/screenshots';
import { formatDate } from '@/lib/formatters';
import type { ScreenshotListItem } from '@/lib/types';

interface ScreenshotCardProps {
  screenshot: ScreenshotListItem;
  index: number;
  onSelect: (index: number, shiftKey?: boolean) => void;
  /** Multi-select mode: a click toggles the card instead of opening it. */
  selecting?: boolean;
  selected?: boolean;
}

// Memoized: loading another page or toggling a favourite re-renders only the cards that changed.
export const ScreenshotCard = memo(function ScreenshotCard({
  screenshot,
  index,
  onSelect,
  selecting = false,
  selected = false,
}: ScreenshotCardProps) {
  return (
    <button
      type="button"
      onClick={(event) => onSelect(index, event.shiftKey)}
      title={formatDate(screenshot.taken_at ?? screenshot.uploaded_at)}
      aria-pressed={selecting ? selected : undefined}
      className={`group relative block w-full aspect-video overflow-hidden rounded-sm bg-bg-tertiary outline-2 -outline-offset-2 hover:outline-accent-primary focus-visible:outline-accent-primary transition-[outline-color] ${
        selected ? 'outline-accent-primary' : 'outline-transparent'
      }`}
    >
      {/* No frame or caption: the capture is the card. The date is in the tooltip and the viewer. */}
      <img
        src={getThumbnailUrl(screenshot.id, 'sm', screenshot.media_version)}
        srcSet={`${getThumbnailUrl(screenshot.id, 'sm', screenshot.media_version)} 300w, ${getThumbnailUrl(screenshot.id, 'md', screenshot.media_version)} 800w`}
        sizes="(min-width: 1280px) 16vw, (min-width: 1024px) 20vw, (min-width: 768px) 25vw, (min-width: 640px) 33vw, 50vw"
        alt={screenshot.filename}
        className="h-full w-full object-cover"
        loading="lazy"
        decoding="async"
      />

      {selecting && (
        <>
          {selected && <div className="absolute inset-0 bg-accent-primary/25" aria-hidden="true" />}
          <div
            aria-hidden="true"
            className={`absolute top-2 left-2 flex h-5 w-5 items-center justify-center rounded-full border-2 ${
              selected ? 'bg-accent-primary border-accent-primary text-on-accent' : 'border-white/80 bg-black/40'
            }`}
          >
            {selected && <Check className="h-3 w-3" strokeWidth={3} />}
          </div>
        </>
      )}

      {/* Badges overlay */}
      <div className="absolute top-2 right-2 flex items-center gap-1.5">
        {(screenshot.variant_count ?? 1) > 1 && (
          <div className="flex items-center gap-1 bg-black/75 rounded-full px-1.5 py-1 text-white/80">
            <Files className="h-3 w-3" />
            <span className="text-[10px] leading-none">{screenshot.variant_count}</span>
          </div>
        )}
        {screenshot.is_favorite && (
          <div className="bg-black/75 rounded-full p-1">
            <Heart className="h-3 w-3 text-red-400 fill-red-400" />
          </div>
        )}
        {screenshot.has_annotation && (
          <div className="bg-black/75 rounded-full p-1">
            <MessageSquare className="h-3 w-3 text-accent-primary" />
          </div>
        )}
      </div>
    </button>
  );
});
