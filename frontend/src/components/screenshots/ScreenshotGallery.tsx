import { ScreenshotCard } from './ScreenshotCard';
import { VirtualizedScreenshotGrid } from './VirtualizedScreenshotGrid';
import type { ScreenshotListItem } from '@/lib/types';

interface ScreenshotGalleryProps {
  screenshots: ScreenshotListItem[];
  onSelect: (index: number, shiftKey?: boolean) => void;
  onEndReached?: () => void;
  selecting?: boolean;
  selectedIds?: ReadonlySet<number>;
}

export function ScreenshotGallery({
  screenshots,
  onSelect,
  onEndReached,
  selecting = false,
  selectedIds,
}: ScreenshotGalleryProps) {
  return (
    <VirtualizedScreenshotGrid
      screenshots={screenshots}
      onSelect={onSelect}
      onEndReached={onEndReached}
      renderItem={(screenshot, index, select) => (
        <ScreenshotCard
          screenshot={screenshot}
          index={index}
          onSelect={select}
          selecting={selecting}
          selected={selecting && !!selectedIds?.has(screenshot.id)}
        />
      )}
    />
  );
}
