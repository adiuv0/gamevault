import { Files, Heart, MessageSquare } from 'lucide-react';
import { getThumbnailUrl } from '@/api/screenshots';
import { formatDate } from '@/lib/formatters';
import type { ScreenshotListItem } from '@/lib/types';

interface ScreenshotCardProps {
  screenshot: ScreenshotListItem;
  onClick: () => void;
}

export function ScreenshotCard({ screenshot, onClick }: ScreenshotCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group block w-full rounded-lg overflow-hidden bg-bg-secondary border border-border hover:border-accent-primary/50 transition-all duration-200 hover:scale-[1.02] hover:shadow-lg hover:shadow-accent-primary/5 text-left"
    >
      {/* Thumbnail */}
      <div className="relative aspect-video bg-bg-tertiary overflow-hidden">
        <img
          src={getThumbnailUrl(screenshot.id, 'sm')}
          srcSet={`${getThumbnailUrl(screenshot.id, 'sm')} 300w, ${getThumbnailUrl(screenshot.id, 'md')} 800w`}
          sizes="(min-width: 1280px) 16vw, (min-width: 1024px) 20vw, (min-width: 768px) 25vw, (min-width: 640px) 33vw, 50vw"
          alt={screenshot.filename}
          className="w-full h-full object-cover group-hover:brightness-110 transition-all duration-200"
          loading="lazy"
          decoding="async"
        />

        {/* Badges overlay */}
        <div className="absolute top-2 right-2 flex items-center gap-1.5">
          {(screenshot.variant_count ?? 1) > 1 && (
            <div className="flex items-center gap-1 bg-black/70 backdrop-blur-sm rounded-full px-1.5 py-1 text-white/80">
              <Files className="h-3 w-3" />
              <span className="text-[10px] leading-none">{screenshot.variant_count}</span>
            </div>
          )}
          {screenshot.is_favorite && (
            <div className="bg-black/70 backdrop-blur-sm rounded-full p-1">
              <Heart className="h-3 w-3 text-red-400 fill-red-400" />
            </div>
          )}
          {screenshot.has_annotation && (
            <div className="bg-black/70 backdrop-blur-sm rounded-full p-1">
              <MessageSquare className="h-3 w-3 text-accent-primary" />
            </div>
          )}
        </div>

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
