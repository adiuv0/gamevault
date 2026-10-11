import { VirtuosoGrid } from 'react-virtuoso';
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { ScreenshotListItem } from '@/lib/types';

interface VirtualizedScreenshotGridProps {
  screenshots: ScreenshotListItem[];
  onSelect: (index: number) => void;
  onEndReached?: () => void;
  /** Cards receive the index and call onSelect themselves so they can be memoized. */
  renderItem: (screenshot: ScreenshotListItem, index: number, onSelect: (index: number) => void) => ReactNode;
}

function findScrollParent(element: HTMLElement | null): HTMLElement | null {
  let ancestor = element?.parentElement ?? null;

  while (ancestor) {
    const overflowY = window.getComputedStyle(ancestor).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay') {
      return ancestor;
    }
    ancestor = ancestor.parentElement;
  }

  return null;
}

export function VirtualizedScreenshotGrid({
  screenshots,
  onSelect,
  onEndReached,
  renderItem,
}: VirtualizedScreenshotGridProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const endSentinelRef = useRef<HTMLDivElement>(null);
  const [scrollParent, setScrollParent] = useState<HTMLElement | null>();

  useLayoutEffect(() => {
    setScrollParent(findScrollParent(containerRef.current));
  }, []);

  useEffect(() => {
    const sentinel = endSentinelRef.current;
    if (scrollParent === undefined || !sentinel || !onEndReached) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) onEndReached();
      },
      {
        root: scrollParent,
        rootMargin: '0px 0px 800px 0px',
      },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [onEndReached, screenshots.length, scrollParent]);

  return (
    <div ref={containerRef}>
      {scrollParent !== undefined && (
        <VirtuosoGrid
          useWindowScroll={scrollParent === null}
          customScrollParent={scrollParent ?? undefined}
          data={screenshots}
          computeItemKey={(_, screenshot) => screenshot.id}
          listClassName="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3"
          itemClassName="min-w-0"
          increaseViewportBy={{ top: 400, bottom: 800 }}
          rangeChanged={({ endIndex }) => {
            if (endIndex >= screenshots.length - 12) onEndReached?.();
          }}
          itemContent={(index, screenshot) => renderItem(screenshot, index, onSelect)}
        />
      )}
      <div ref={endSentinelRef} className="h-px" aria-hidden="true" />
    </div>
  );
}
