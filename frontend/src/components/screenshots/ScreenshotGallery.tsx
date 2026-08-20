import { ScreenshotCard } from './ScreenshotCard';
import { VirtualizedScreenshotGrid } from './VirtualizedScreenshotGrid';
import type { ScreenshotListItem } from '@/lib/types';

interface ScreenshotGalleryProps {
  screenshots: ScreenshotListItem[];
  onSelect: (index: number) => void;
  onEndReached?: () => void;
}

export function ScreenshotGallery({
  screenshots,
  onSelect,
  onEndReached,
}: ScreenshotGalleryProps) {
  return (
    <VirtualizedScreenshotGrid
      screenshots={screenshots}
      onSelect={onSelect}
      onEndReached={onEndReached}
      renderItem={(screenshot, onClick) => (
        <ScreenshotCard
          screenshot={screenshot}
          onClick={onClick}
        />
      )}
    />
  );
}
