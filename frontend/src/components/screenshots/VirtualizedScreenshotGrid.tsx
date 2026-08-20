import { VirtuosoGrid } from 'react-virtuoso';
import type { ReactNode } from 'react';
import type { ScreenshotListItem } from '@/lib/types';

interface VirtualizedScreenshotGridProps {
  screenshots: ScreenshotListItem[];
  onSelect: (index: number) => void;
  onEndReached?: () => void;
  renderItem: (screenshot: ScreenshotListItem, onClick: () => void) => ReactNode;
}

export function VirtualizedScreenshotGrid({
  screenshots,
  onSelect,
  onEndReached,
  renderItem,
}: VirtualizedScreenshotGridProps) {
  return (
    <VirtuosoGrid
      useWindowScroll
      data={screenshots}
      computeItemKey={(_, screenshot) => screenshot.id}
      listClassName="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3"
      itemClassName="min-w-0"
      increaseViewportBy={{ top: 400, bottom: 800 }}
      rangeChanged={({ endIndex }) => {
        if (endIndex >= screenshots.length - 12) onEndReached?.();
      }}
      itemContent={(index, screenshot) =>
        renderItem(screenshot, () => onSelect(index))
      }
    />
  );
}
