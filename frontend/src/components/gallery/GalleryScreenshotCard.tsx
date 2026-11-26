import { memo } from 'react';
import { Files } from 'lucide-react';
import { galleryThumbUrl } from '@/api/gallery';
import { formatDate } from '@/lib/formatters';
import type { ScreenshotListItem } from '@/lib/types';

interface GalleryScreenshotCardProps {
  screenshot: ScreenshotListItem;
  index: number;
  onSelect: (index: number) => void;
}

// Memoized: loading another page re-renders only the new cards.
export const GalleryScreenshotCard = memo(function GalleryScreenshotCard({
  screenshot,
  index,
  onSelect,
}: GalleryScreenshotCardProps) {
  return (
    <button
      type="button"
      onClick={() => onSelect(index)}
      title={formatDate(screenshot.taken_at ?? screenshot.uploaded_at)}
      className="group relative block w-full aspect-video overflow-hidden rounded-sm bg-bg-tertiary outline-2 -outline-offset-2 outline-transparent hover:outline-accent-primary focus-visible:outline-accent-primary transition-[outline-color]"
    >
      {/* No frame or caption: the capture is the card. The date is in the tooltip and the viewer. */}
      <img
        src={galleryThumbUrl(screenshot.id, 'sm', screenshot.media_version)}
        srcSet={`${galleryThumbUrl(screenshot.id, 'sm', screenshot.media_version)} 300w, ${galleryThumbUrl(screenshot.id, 'md', screenshot.media_version)} 800w`}
        sizes="(min-width: 1280px) 16vw, (min-width: 1024px) 20vw, (min-width: 768px) 25vw, (min-width: 640px) 33vw, 50vw"
        alt={screenshot.filename}
        className="h-full w-full object-cover"
        loading="lazy"
        decoding="async"
      />

      {(screenshot.variant_count ?? 1) > 1 && (
        <div className="absolute top-2 right-2 flex items-center gap-1 bg-black/75 rounded-full px-1.5 py-1 text-white/80">
          <Files className="h-3 w-3" />
          <span className="text-[10px] leading-none">{screenshot.variant_count}</span>
        </div>
      )}
    </button>
  );
});
