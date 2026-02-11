import { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Upload, Image, Trash2, Loader2, RefreshCw, CheckCircle2, Globe, Lock } from 'lucide-react';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { EmptyState } from '@/components/shared/EmptyState';
import { ScreenshotGallery } from '@/components/screenshots/ScreenshotGallery';
import { ScreenshotViewer } from '@/components/screenshots/ScreenshotViewer';
import { getGame, deleteGame, updateGame, getCoverUrl } from '@/api/games';
import { listScreenshots } from '@/api/screenshots';
import { fetchGameMetadata } from '@/api/metadata';
import { formatCount, formatDate } from '@/lib/formatters';
import type { Game, Screenshot } from '@/lib/types';

export function GamePage() {
  const { id } = useParams<{ id: string }>();
  const gameId = Number(id);

  const [game, setGame] = useState<Game | null>(null);
  const [screenshots, setScreenshots] = useState<Screenshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [fetchingMeta, setFetchingMeta] = useState(false);
  const [metaResult, setMetaResult] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      setLoading(true);
      setError(null);
      const [gameData, screenshotData] = await Promise.all([
        getGame(gameId),
        listScreenshots(gameId, 1, 50),
      ]);
      setGame(gameData);
      setScreenshots(screenshotData.screenshots);
      setHasMore(screenshotData.has_more);
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load game');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [gameId]);

  const loadMore = async () => {
    if (loadingMore || !hasMore) return;
    try {
      setLoadingMore(true);
      const nextPage = page + 1;
      const data = await listScreenshots(gameId, nextPage, 50);
      setScreenshots((prev) => [...prev, ...data.screenshots]);
      setHasMore(data.has_more);
      setPage(nextPage);
    } catch {
      // ignore
    } finally {
      setLoadingMore(false);
    }
  };

  const handleFavoriteToggle = useCallback((screenshotId: number, isFav: boolean) => {
    setScreenshots((prev) =>
      prev.map((s) =>
        s.id === screenshotId ? { ...s, is_favorite: isFav } : s,
      ),
    );
  }, []);

  const handleDelete = async () => {
    try {
      setDeleting(true);
      await deleteGame(gameId);
      window.location.href = '/';
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete game');
      setDeleting(false);
      setShowDeleteConfirm(false);
    }
  };

  const handleFetchMetadata = async () => {
    try {
      setFetchingMeta(true);
      setMetaResult(null);
      const result = await fetchGameMetadata(gameId);
      const fields = result.fields_updated;
      if (fields.length > 0 || result.cover_downloaded) {
        const parts = [];
        if (fields.length > 0) parts.push(`Updated: ${fields.join(', ')}`);
        if (result.cover_downloaded) parts.push('cover downloaded');
        setMetaResult(parts.join('; '));
        // Refresh game data to show new metadata
        const freshGame = await getGame(gameId);
        setGame(freshGame);
      } else {
        setMetaResult(result.sources_succeeded.length > 0
          ? 'No new metadata found'
          : 'No metadata sources available (add API keys in Settings)');
      }
    } catch {
      setMetaResult('Failed to fetch metadata');
    } finally {
      setFetchingMeta(false);
      // Clear result message after 5 seconds
      setTimeout(() => setMetaResult(null), 5000);
    }
  };

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
          to="/"
          className="px-4 py-2 bg-bg-tertiary text-text-primary rounded-md text-sm hover:bg-bg-tertiary/80 transition-colors no-underline"
        >
          Back to Library
        </Link>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text-primary transition-colors no-underline mb-3"
        >
          <ArrowLeft className="h-4 w-4" />
          Library
        </Link>

        <div className="flex items-start justify-between">
          <div className="flex items-start gap-4">
            {/* Cover */}
            {game.cover_image_path ? (
              <img
                src={getCoverUrl(game.id)}
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

          <div className="flex items-center gap-2">
            <button
              onClick={async () => {
                const updated = await updateGame(gameId, { is_public: !game.is_public });
                setGame(updated);
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-border text-text-secondary rounded-md text-sm hover:text-text-primary hover:border-accent-primary/50 transition-colors"
              title={game.is_public ? 'Visible in public gallery' : 'Hidden from public gallery'}
            >
              {game.is_public ? (
                <Globe className="h-3.5 w-3.5" />
              ) : (
                <Lock className="h-3.5 w-3.5" />
              )}
              {game.is_public ? 'Public' : 'Private'}
            </button>
            <button
              onClick={handleFetchMetadata}
              disabled={fetchingMeta}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-border text-text-secondary rounded-md text-sm hover:text-text-primary hover:border-accent-primary/50 transition-colors disabled:opacity-50"
              title="Fetch metadata from Steam/SteamGridDB/IGDB"
            >
              {fetchingMeta ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              {fetchingMeta ? 'Fetching...' : 'Metadata'}
            </button>
            <Link
              to={`/upload?game=${game.id}`}
              className="flex items-center gap-2 px-3 py-1.5 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors no-underline"
            >
              <Upload className="h-4 w-4" />
              Upload
            </Link>
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="p-1.5 rounded-md text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 transition-colors"
              title="Delete game"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Metadata result toast */}
      {metaResult && (
        <div className="mb-4 flex items-center gap-2 px-3 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-secondary">
          <CheckCircle2 className="h-4 w-4 text-accent-success flex-shrink-0" />
          {metaResult}
        </div>
      )}

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
      {screenshots.length === 0 ? (
        <EmptyState
          icon={<Image className="h-12 w-12" />}
          title="No screenshots yet"
          description="Upload screenshots to this game to start building your collection."
          action={
            <Link
              to={`/upload?game=${game.id}`}
              className="px-4 py-2 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors no-underline"
            >
              Upload Screenshots
            </Link>
          }
        />
      ) : (
        <>
          <ScreenshotGallery
            screenshots={screenshots}
            onSelect={(index) => setViewerIndex(index)}
          />

          {/* Load more */}
          {hasMore && (
            <div className="flex justify-center mt-6">
              <button
                onClick={loadMore}
                disabled={loadingMore}
                className="flex items-center gap-2 px-6 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-primary hover:bg-bg-tertiary transition-colors disabled:opacity-50"
              >
                {loadingMore && <Loader2 className="h-4 w-4 animate-spin" />}
                Load More
              </button>
            </div>
          )}
        </>
      )}

      {/* Lightbox */}
      {viewerIndex !== null && (
        <ScreenshotViewer
          screenshots={screenshots}
          currentIndex={viewerIndex}
          onClose={() => setViewerIndex(null)}
          onNavigate={setViewerIndex}
          onFavoriteToggle={handleFavoriteToggle}
          onAnnotationChanged={(id, has) => {
            setScreenshots((prev) =>
              prev.map((s) =>
                s.id === id ? { ...s, has_annotation: has } : s,
              ),
            );
          }}
        />
      )}

      {/* Delete confirmation modal */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setShowDeleteConfirm(false)}
          />
          <div className="relative bg-bg-secondary border border-border rounded-lg p-6 w-full max-w-sm shadow-xl">
            <h2 className="text-lg font-semibold text-text-primary mb-2">Delete Game</h2>
            <p className="text-sm text-text-secondary mb-4">
              Are you sure you want to delete <strong>{game.name}</strong> and all its screenshots? This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 bg-bg-tertiary text-text-primary rounded-md text-sm hover:bg-bg-tertiary/80 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-4 py-2 bg-accent-danger text-white rounded-md text-sm font-medium hover:bg-accent-danger/90 transition-colors disabled:opacity-50 flex items-center gap-2"
              >
                {deleting && <Loader2 className="h-3 w-3 animate-spin" />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
