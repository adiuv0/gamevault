import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Image, Loader2, RefreshCw } from 'lucide-react';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { EmptyState } from '@/components/shared/EmptyState';
import { GalleryScreenshotCard } from '@/components/gallery/GalleryScreenshotCard';
import { GalleryScreenshotViewer } from '@/components/gallery/GalleryScreenshotViewer';
import { VirtualizedScreenshotGrid } from '@/components/screenshots/VirtualizedScreenshotGrid';
import {
  ScreenshotSortControl,
  type ScreenshotSort,
} from '@/components/screenshots/ScreenshotSortControl';
import {
  galleryCoverUrl,
  galleryGetGame,
  galleryListScreenshotsCursor,
} from '@/api/gallery';
import { formatCount, formatDate } from '@/lib/formatters';
import type { Game, ScreenshotListItem } from '@/lib/types';

export function GalleryGamePage() {
  const { id } = useParams<{ id: string }>();
  const gameId = Number(id);

  const [game, setGame] = useState<Game | null>(null);
  const [screenshots, setScreenshots] = useState<ScreenshotListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState(false);
  const loadingMoreRef = useRef(false);
  const requestVersionRef = useRef(0);
  const [screenshotSort, setScreenshotSort] = useState<ScreenshotSort>('date_desc');

  useEffect(() => {
    const requestVersion = ++requestVersionRef.current;
    loadingMoreRef.current = false;
    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        const [gameData, screenshotData] = await Promise.all([
          galleryGetGame(gameId),
          galleryListScreenshotsCursor(gameId, null, 50, screenshotSort),
        ]);
        if (requestVersion !== requestVersionRef.current) return;
        setGame(gameData);
        setScreenshots(screenshotData.screenshots);
        setNextCursor(screenshotData.next_cursor);
        setLoadMoreError(false);
        setViewerIndex(null);
      } catch (err) {
        if (requestVersion !== requestVersionRef.current) return;
        setError(err instanceof Error ? err.message : 'Failed to load game');
      } finally {
        if (requestVersion === requestVersionRef.current) setLoading(false);
      }
    };
    fetchData();
  }, [gameId, screenshotSort]);

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !nextCursor) return;
    const requestVersion = requestVersionRef.current;
    loadingMoreRef.current = true;
    try {
      setLoadingMore(true);
      setLoadMoreError(false);
      const data = await galleryListScreenshotsCursor(
        gameId,
        nextCursor,
        50,
        screenshotSort,
      );
      if (requestVersion !== requestVersionRef.current) return;
      loadingMoreRef.current = false;
      setLoadingMore(false);
      setScreenshots((current) => {
        const existingIds = new Set(current.map((item) => item.id));
        return [...current, ...data.screenshots.filter((item) => !existingIds.has(item.id))];
      });
      setNextCursor(data.next_cursor);
    } catch {
      if (requestVersion === requestVersionRef.current) setLoadMoreError(true);
    } finally {
      if (requestVersion === requestVersionRef.current) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    }
  }, [gameId, nextCursor, screenshotSort]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error || !game) {
    return (
      <div className="text-center py-12">
        <p className="text-accent-danger mb-4">{error || 'Game not found'}</p>
        <Link
          to="/gallery"
          className="px-4 py-2 bg-bg-tertiary text-text-primary rounded-md text-sm hover:bg-bg-tertiary/80 transition-colors no-underline"
        >
          Back to Gallery
        </Link>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/gallery"
          className="inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text-primary transition-colors no-underline mb-3"
        >
          <ArrowLeft className="h-4 w-4" />
          Gallery
        </Link>

        <div className="flex items-start gap-4">
          {/* Cover */}
          {game.cover_image_path ? (
            <img
              src={galleryCoverUrl(game.id)}
              alt={game.name}
              className="w-16 h-24 rounded-lg object-cover flex-shrink-0 hidden sm:block"
            />
          ) : (
            <div className="w-16 h-24 rounded-lg bg-bg-tertiary flex items-center justify-center flex-shrink-0 hidden sm:block">
              <Image className="h-6 w-6 text-text-muted" />
            </div>
          )}

          <div>
            <h1 className="text-2xl font-bold text-text-primary">{game.name}</h1>
            <div className="flex items-center gap-3 mt-1 text-sm text-text-muted">
              <span>{formatCount(game.screenshot_count)}</span>
              {game.developer && <span>{game.developer}</span>}
              {game.last_screenshot_date && (
                <span>Last: {formatDate(game.last_screenshot_date)}</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Game description */}
      {game.description && (
        <div className="mb-6 text-sm text-text-secondary leading-relaxed max-w-3xl">
          {game.description}
        </div>
      )}

      {/* Metadata chips */}
      {(game.genres || game.release_date || game.publisher) && (
        <div className="flex flex-wrap gap-2 mb-6">
          {game.genres && (
            <span className="px-2 py-0.5 bg-bg-tertiary text-text-muted text-xs rounded-full">
              {game.genres}
            </span>
          )}
          {game.release_date && (
            <span className="px-2 py-0.5 bg-bg-tertiary text-text-muted text-xs rounded-full">
              {game.release_date}
            </span>
          )}
          {game.publisher && game.publisher !== game.developer && (
            <span className="px-2 py-0.5 bg-bg-tertiary text-text-muted text-xs rounded-full">
              {game.publisher}
            </span>
          )}
        </div>
      )}

      {/* Screenshots */}
      {screenshots.length > 0 && (
        <ScreenshotSortControl value={screenshotSort} onChange={setScreenshotSort} />
      )}
      {screenshots.length === 0 ? (
        <EmptyState
          icon={<Image className="h-12 w-12" />}
          title="No screenshots"
          description="This game doesn't have any screenshots yet."
        />
      ) : (
        <>
          <VirtualizedScreenshotGrid
            screenshots={screenshots}
            onSelect={setViewerIndex}
            onEndReached={nextCursor ? loadMore : undefined}
            renderItem={(screenshot, onClick) => (
              <GalleryScreenshotCard
                screenshot={screenshot}
                onClick={onClick}
              />
            )}
          />

          {loadingMore && (
            <div className="flex justify-center mt-6" aria-live="polite">
              <Loader2 className="h-5 w-5 animate-spin text-text-muted" />
              <span className="sr-only">Loading more screenshots</span>
            </div>
          )}
          {loadMoreError && nextCursor && (
            <div className="flex justify-center mt-6">
              <button
                onClick={() => void loadMore()}
                className="inline-flex items-center gap-2 px-4 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-primary hover:bg-bg-tertiary transition-colors"
              >
                <RefreshCw className="h-4 w-4" />
                Retry
              </button>
            </div>
          )}
          {nextCursor && !loadingMore && !loadMoreError && (
            <div className="flex justify-center mt-6">
              <button
                onClick={() => void loadMore()}
                className="inline-flex items-center gap-2 px-4 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-primary hover:bg-bg-tertiary transition-colors"
              >
                Load more
              </button>
            </div>
          )}
        </>
      )}

      {/* Lightbox */}
      {viewerIndex !== null && (
        <GalleryScreenshotViewer
          screenshots={screenshots}
          currentIndex={viewerIndex}
          onClose={() => setViewerIndex(null)}
          onNavigate={setViewerIndex}
        />
      )}
    </div>
  );
}
