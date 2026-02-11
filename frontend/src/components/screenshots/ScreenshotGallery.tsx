import { ScreenshotCard } from './ScreenshotCard';
import type { Screenshot } from '@/lib/types';

interface ScreenshotGalleryProps {
  screenshots: Screenshot[];
  onSelect: (index: number) => void;
}

export function ScreenshotGallery({ screenshots, onSelect }: ScreenshotGalleryProps) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
      {screenshots.map((screenshot, index) => (
        <ScreenshotCard
          key={screenshot.id}
          screenshot={screenshot}
          onClick={() => onSelect(index)}
        />
      ))}
    </div>
  );
}
