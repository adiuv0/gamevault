import { galleryThumbUrl } from '@/api/gallery';
import { formatDate } from '@/lib/formatters';
import type { Screenshot } from '@/lib/types';

interface GalleryScreenshotCardProps {
  screenshot: Screenshot;
  onClick: () => void;
}

export function GalleryScreenshotCard({ screenshot, onClick }: GalleryScreenshotCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group block w-full rounded-lg overflow-hidden bg-bg-secondary border border-border hover:border-accent-primary/50 transition-all duration-200 hover:scale-[1.02] hover:shadow-lg hover:shadow-accent-primary/5 text-left"
    >
      {/* Thumbnail */}
      <div className="relative aspect-video bg-bg-tertiary overflow-hidden">
        <img
          src={galleryThumbUrl(screenshot.id, 'md')}
          alt={screenshot.filename}
          className="w-full h-full object-cover group-hover:brightness-110 transition-all duration-200"
          loading="lazy"
        />

        {/* Hover overlay */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-200" />
      </div>

      {/* Info */}
      <div className="p-2">
        <p className="text-xs text-text-muted truncate">
          {screenshot.taken_at ? formatDate(screenshot.taken_at) : formatDate(screenshot.uploaded_at)}
        </p>
      </div>
    </button>
  );
}
