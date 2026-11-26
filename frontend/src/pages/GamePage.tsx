import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { ArrowLeft, Upload, Image, Trash2, Loader2, RefreshCw, CheckCircle2, AlertCircle, Globe, Lock, GitMerge, Search, CheckSquare, X } from 'lucide-react';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { EmptyState } from '@/components/shared/EmptyState';
import { ConfirmDialog } from '@/components/shared/ConfirmDialog';
import { ScreenshotGallery } from '@/components/screenshots/ScreenshotGallery';
import { ScreenshotViewer } from '@/components/screenshots/ScreenshotViewer';
import {
  ScreenshotSortControl,
  type ScreenshotSort,
} from '@/components/screenshots/ScreenshotSortControl';
import { getGame, deleteGame, updateGame, getCoverUrl, listGames, mergeGameInto } from '@/api/games';
import {
  listScreenshotsCursor,
  getScreenshotPreviewUrl,
  getScreenshot,
  deleteScreenshots,
  type DeleteResult,
} from '@/api/screenshots';
import { fetchGameMetadata } from '@/api/metadata';
import { formatCount, formatDate, formatMonthYear, isHexColor } from '@/lib/formatters';
import type { Game, ScreenshotListItem } from '@/lib/types';

export function GamePage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const gameId = Number(id);

  const [game, setGame] = useState<Game | null>(null);
  // The route id the loaded game answers (merged games redirect to another id).
  const [loadedRouteId, setLoadedRouteId] = useState<number | null>(null);
  const [screenshots, setScreenshots] = useState<ScreenshotListItem[]>([]);
  // The capture behind the title. Chosen once per game, so re-sorting doesn't swap it.
  const [hero, setHero] = useState<ScreenshotListItem | null>(null);
  const heroGameRef = useRef<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState(false);
  const loadingMoreRef = useRef(false);
  const requestVersionRef = useRef(0);
  const [screenshotSort, setScreenshotSort] = useState<ScreenshotSort>('date_desc');
  const [deleting, setDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [fetchingMeta, setFetchingMeta] = useState(false);
  const [metaResult, setMetaResult] = useState<string | null>(null);
  const [metaFailed, setMetaFailed] = useState(false);
  const [showMergeModal, setShowMergeModal] = useState(false);
  const metaResultTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Multi-select in the grid.
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<number>>(new Set());
  const lastSelectedIndex = useRef<number | null>(null);
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkDeleteError, setBulkDeleteError] = useState<string | null>(null);

  useEffect(() => () => clearTimeout(metaResultTimer.current), []);

  const fetchData = useCallback(async () => {
    const requestVersion = ++requestVersionRef.current;
    loadingMoreRef.current = false;
    try {
      setLoading(true);
      setError(null);
      const [gameData, screenshotData] = await Promise.all([
        getGame(gameId),
        listScreenshotsCursor(gameId, null, 50, screenshotSort),
      ]);
      if (requestVersion !== requestVersionRef.current) return;
      setGame(gameData);
      setLoadedRouteId(gameId);
      setScreenshots(screenshotData.screenshots);
      if (heroGameRef.current !== gameId || screenshotData.screenshots.length === 0) {
        heroGameRef.current = gameId;
        // A favourite if one is near the top, otherwise the first capture listed.
        setHero(
          screenshotData.screenshots.find((item) => item.is_favorite)
            ?? screenshotData.screenshots[0]
            ?? null,
        );
      }
      setNextCursor(screenshotData.next_cursor);
      setLoadMoreError(false);
      setViewerIndex(null);
      setSelectedIds(new Set());
      lastSelectedIndex.current = null;
    } catch (err) {
      if (requestVersion !== requestVersionRef.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load game');
    } finally {
      if (requestVersion === requestVersionRef.current) setLoading(false);
    }
  }, [gameId, screenshotSort]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !nextCursor) return;
    const requestVersion = requestVersionRef.current;
    loadingMoreRef.current = true;
    try {
      setLoadingMore(true);
      setLoadMoreError(false);
      const data = await listScreenshotsCursor(gameId, nextCursor, 50, screenshotSort);
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

  const handleFavoriteToggle = useCallback((screenshotId: number, isFav: boolean) => {
    setScreenshots((prev) =>
      prev.map((s) =>
        s.id === screenshotId ? { ...s, is_favorite: isFav } : s,
      ),
    );
  }, []);

  const stopSelecting = useCallback(() => {
    setSelecting(false);
    setSelectedIds(new Set());
    lastSelectedIndex.current = null;
  }, []);

  useEffect(() => {
    if (!selecting) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && viewerIndex === null) stopSelecting();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [selecting, stopSelecting, viewerIndex]);

  // Read through refs so the card callback stays stable: memoized cards then
  // re-render only when their own selection changes, not on every page load.
  const listRef = useRef(screenshots);
  const selectingRef = useRef(selecting);
  useEffect(() => {
    listRef.current = screenshots;
    selectingRef.current = selecting;
  }, [screenshots, selecting]);

  const handleCardSelect = useCallback((index: number, shiftKey?: boolean) => {
    if (!selectingRef.current) {
      setViewerIndex(index);
      return;
    }
    const from = lastSelectedIndex.current;
    const screenshots = listRef.current;
    setSelectedIds((current) => {
      const next = new Set(current);
      const target = screenshots[index];
      if (!target) return current;
      if (shiftKey && from !== null) {
        // Shift-click selects the whole run from the last card clicked.
        const [start, end] = from < index ? [from, index] : [index, from];
        for (const item of screenshots.slice(start, end + 1)) next.add(item.id);
      } else if (next.has(target.id)) {
        next.delete(target.id);
      } else {
        next.add(target.id);
      }
      return next;
    });
    lastSelectedIndex.current = index;
  }, []);

  // Counts and first/last capture dates come from the server after any delete.
  const refreshGame = useCallback(() => {
    getGame(gameId).then(setGame).catch(() => undefined);
  }, [gameId]);

  const removeCaptures = useCallback((ids: ReadonlySet<number>) => {
    setScreenshots((current) => current.filter((item) => !ids.has(item.id)));
    setHero((current) => (current && ids.has(current.id) ? null : current));
  }, []);

  const handleViewerDeleted = useCallback(async (id: number, result: DeleteResult) => {
    if (result.deleted_captures === 0) {
      // One file of the capture went; the capture stays, maybe shown by another file now.
      const remainingId = result.remaining_id ?? id;
      try {
        const capture = await getScreenshot(remainingId);
        setScreenshots((current) => current.map((item) => (item.id === id ? {
          ...item,
          id: capture.id,
          filename: capture.filename,
          media_version: capture.media_version,
          taken_at: capture.taken_at,
          uploaded_at: capture.uploaded_at,
          is_favorite: capture.is_favorite,
          has_annotation: capture.has_annotation,
          variant_count: capture.variant_count,
        } : item)));
      } catch {
        void fetchData();
      }
      refreshGame();
      return;
    }
    const remaining = screenshots.filter((item) => item.id !== id);
    removeCaptures(new Set([id]));
    // Stay at the same spot: the next capture slides into it.
    setViewerIndex((index) => {
      if (index === null || remaining.length === 0) return null;
      return Math.min(index, remaining.length - 1);
    });
    refreshGame();
  }, [fetchData, refreshGame, removeCaptures, screenshots]);

  const handleBulkDelete = async () => {
    if (selectedIds.size === 0) return;
    setBulkDeleting(true);
    setBulkDeleteError(null);
    try {
      await deleteScreenshots([...selectedIds]);
      removeCaptures(selectedIds);
      setConfirmBulkDelete(false);
      stopSelecting();
      refreshGame();
    } catch (err) {
      setBulkDeleteError(err instanceof Error ? err.message : 'Could not delete the screenshots');
    } finally {
      setBulkDeleting(false);
    }
  };

  const handleDelete = async () => {
    try {
      setDeleting(true);
      await deleteGame(gameId);
      navigate('/', { replace: true });
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
      setMetaFailed(false);
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
      setMetaFailed(true);
      setMetaResult('Failed to fetch metadata');
    } finally {
      setFetchingMeta(false);
      // Clear result message after 5 seconds
      clearTimeout(metaResultTimer.current);
      metaResultTimer.current = setTimeout(() => setMetaResult(null), 5000);
    }
  };

  // Only blank the page while a different game is loading. Re-sorting or
  // refreshing after an ungroup keeps the grid (and its scroll position) mounted.
  if (loading && (!game || loadedRouteId !== gameId)) {
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

  const heroShot = hero ?? screenshots.find((item) => item.is_favorite) ?? screenshots[0] ?? null;
  const accentStyle = isHexColor(game.accent_color)
    ? ({ '--color-accent-primary': game.accent_color } as CSSProperties)
    : undefined;
  const longName = game.name.length > 28;
  const facts = [
    game.developer,
    game.publisher && game.publisher !== game.developer ? game.publisher : null,
    game.release_date,
    game.genres,
  ].filter(Boolean);
  const secondaryButton =
    'flex items-center gap-1.5 px-3 py-1.5 border border-border bg-bg-primary/60 text-text-secondary rounded-md text-sm hover:text-text-primary hover:border-accent-primary/50 transition-colors disabled:opacity-50';

  return (
    // The accent set here reaches everything on the page, the viewer and dialogs included.
    <div style={accentStyle}>
      {/* Hero: one of the game's own captures behind its title. Bleeds to the edges of the page. */}
      <header className="relative -mx-4 -mt-4 sm:-mx-6 sm:-mt-6 mb-5 overflow-hidden">
        {heroShot && (
          <div className="absolute inset-0" aria-hidden="true">
            <img
              src={getScreenshotPreviewUrl(heroShot.id, heroShot.media_version)}
              alt=""
              decoding="async"
              className="h-full w-full object-cover"
            />
            {/* Plain gradients, no blur: readable text over any capture at no scrolling cost. */}
            <div className="absolute inset-0 bg-gradient-to-r from-bg-primary/85 via-bg-primary/35 to-transparent" />
            <div className="absolute inset-0 bg-gradient-to-b from-bg-primary/20 via-bg-primary/45 to-bg-primary" />
          </div>
        )}

        <div className={`relative px-4 sm:px-8 pb-5 ${heroShot ? 'pt-4 sm:pt-5' : 'pt-4 sm:pt-6'}`}>
          <Link
            to="/"
            className="inline-flex items-center gap-1.5 text-sm text-text-secondary hover:text-text-primary transition-colors no-underline"
          >
            <ArrowLeft className="h-4 w-4" />
            Library
          </Link>

          <div className={`flex items-end gap-5 sm:gap-6 ${heroShot ? 'mt-14 sm:mt-24 lg:mt-32' : 'mt-6'}`}>
            {game.cover_image_path ? (
              <img
                src={getCoverUrl(game.id, game.updated_at)}
                alt=""
                className="hidden sm:block w-28 lg:w-32 aspect-[2/3] rounded object-cover flex-shrink-0 shadow-xl shadow-black/60"
              />
            ) : (
              <div className="hidden sm:flex w-28 lg:w-32 aspect-[2/3] rounded bg-bg-tertiary items-center justify-center flex-shrink-0">
                <Image className="h-6 w-6 text-text-muted" />
              </div>
            )}

            <div className="min-w-0 flex-1">
              <h1
                className={`display-title text-text-primary break-words text-balance ${
                  longName ? 'text-3xl sm:text-5xl' : 'text-4xl sm:text-6xl'
                }`}
              >
                {game.name}
              </h1>

              <dl className="flex flex-wrap gap-x-7 gap-y-2 mt-4">
                <Stat value={game.screenshot_count.toLocaleString()} label={game.screenshot_count === 1 ? 'capture' : 'captures'} />
                {game.first_screenshot_date && (
                  <Stat value={formatMonthYear(game.first_screenshot_date)} label="first capture" />
                )}
                {game.last_screenshot_date && (
                  <Stat value={formatDate(game.last_screenshot_date)} label="last capture" />
                )}
              </dl>

              {facts.length > 0 && (
                <p className="mt-3 text-sm text-text-secondary break-words">{facts.join(' · ')}</p>
              )}
            </div>
          </div>
        </div>
      </header>

      {/* Actions */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3 mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/upload?game=${game.id}`}
            className="flex items-center gap-2 px-3 py-1.5 bg-accent-primary text-on-accent rounded-md text-sm font-semibold hover:bg-accent-primary/90 transition-colors no-underline"
          >
            <Upload className="h-4 w-4" />
            Upload
          </Link>
          <button
            onClick={() => setShowMergeModal(true)}
            className={secondaryButton}
            title="Merge this game into another (e.g. consolidate Steam + Special K duplicates)"
          >
            <GitMerge className="h-3.5 w-3.5" />
            Merge
          </button>
          <button
            onClick={async () => {
              try {
                const updated = await updateGame(gameId, { is_public: !game.is_public });
                setGame(updated);
              } catch {
                // The button keeps showing the old state; say why it didn't change.
                setMetaFailed(true);
                setMetaResult("Couldn't change who can see this game");
                clearTimeout(metaResultTimer.current);
                metaResultTimer.current = setTimeout(() => setMetaResult(null), 5000);
              }
            }}
            className={secondaryButton}
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
            className={secondaryButton}
            title="Fetch metadata from Steam/SteamGridDB/IGDB"
          >
            {fetchingMeta ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {fetchingMeta ? 'Fetching...' : 'Metadata'}
          </button>
          <button
            onClick={() => setShowDeleteConfirm(true)}
            className="p-1.5 rounded-md text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 transition-colors"
            title="Delete game"
            aria-label="Delete game"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>

        {screenshots.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => (selecting ? stopSelecting() : setSelecting(true))}
              className={`${secondaryButton} ${selecting ? 'text-text-primary border-accent-primary/60' : ''}`}
              title="Select screenshots to delete several at once"
              aria-pressed={selecting}
            >
              <CheckSquare className="h-3.5 w-3.5" />
              {selecting ? 'Done' : 'Select'}
            </button>
            <ScreenshotSortControl value={screenshotSort} onChange={setScreenshotSort} />
          </div>
        )}
      </div>

      {/* Metadata result toast */}
      {metaResult && (
        <div className="mb-4 flex items-center gap-2 px-3 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-secondary">
          {metaFailed ? (
            <AlertCircle className="h-4 w-4 text-accent-danger flex-shrink-0" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-accent-success flex-shrink-0" />
          )}
          {metaResult}
        </div>
      )}

      {/* Game description */}
      {game.description && (
        <p className="mb-5 text-sm text-text-secondary leading-relaxed max-w-3xl line-clamp-3" title={game.description}>
          {game.description}
        </p>
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
              className="px-4 py-2 bg-accent-primary text-on-accent rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors no-underline"
            >
              Upload Screenshots
            </Link>
          }
        />
      ) : (
        <>
          <ScreenshotGallery
            screenshots={screenshots}
            onSelect={handleCardSelect}
            onEndReached={nextCursor ? loadMore : undefined}
            selecting={selecting}
            selectedIds={selectedIds}
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
        <ScreenshotViewer
          screenshots={screenshots}
          currentIndex={viewerIndex}
          onClose={() => setViewerIndex(null)}
          onNavigate={setViewerIndex}
          onGroupingChanged={() => { setViewerIndex(null); void fetchData(); }}
          onDeleted={(id, result) => void handleViewerDeleted(id, result)}
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

      {/* Multi-select bar: stays in view while scrolling the grid. */}
      {selecting && viewerIndex === null && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 pl-4 pr-2 py-2 rounded-full bg-bg-secondary/95 border border-border shadow-xl shadow-black/50">
          <span className="text-sm text-text-primary tabular-nums whitespace-nowrap" aria-live="polite">
            {selectedIds.size === 0 ? 'Click captures to select' : `${selectedIds.size.toLocaleString()} selected`}
          </span>
          <span className="hidden sm:inline text-xs text-text-muted whitespace-nowrap">Shift-click selects a range</span>
          <button
            onClick={() => { setBulkDeleteError(null); setConfirmBulkDelete(true); }}
            disabled={selectedIds.size === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-accent-danger text-white rounded-full text-sm font-medium hover:bg-accent-danger/90 transition-colors disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </button>
          <button
            onClick={stopSelecting}
            className="p-1.5 rounded-full text-text-muted hover:text-text-primary hover:bg-bg-tertiary transition-colors"
            title="Cancel selection (Esc)"
            aria-label="Cancel selection"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {confirmBulkDelete && (
        <ConfirmDialog
          title={`Delete ${selectedIds.size.toLocaleString()} ${selectedIds.size === 1 ? 'capture' : 'captures'}?`}
          busy={bulkDeleting}
          error={bulkDeleteError}
          onCancel={() => setConfirmBulkDelete(false)}
          actions={[{ label: 'Delete', onClick: () => void handleBulkDelete(), danger: true }]}
        >
          <p>
            Every file of each capture goes, grouped copies included (Steam JPG, Special K JXR and PNG).
            They're removed from GameVault and from the library folder. This can't be undone.
          </p>
        </ConfirmDialog>
      )}

      {/* Merge modal */}
      {showMergeModal && (
        <MergeModal
          source={game}
          onClose={() => setShowMergeModal(false)}
          onMerged={(targetId) => {
            setShowMergeModal(false);
            navigate(`/games/${targetId}`);
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
Delete <strong>{game.name}</strong> and all its screenshots, notes and share links from GameVault? This cannot be undone. The image files themselves stay in the library folder on disk.
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


function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex flex-col-reverse">
      <dt className="mt-1 text-xs text-text-secondary">{label}</dt>
      <dd className="font-display text-xl sm:text-2xl font-semibold leading-none text-text-primary tabular-nums">
        {value}
      </dd>
    </div>
  );
}


// ── Merge modal ─────────────────────────────────────────────────────────────


interface MergeModalProps {
  source: Game;
  onClose: () => void;
  onMerged: (targetId: number) => void;
}

function MergeModal({ source, onClose, onMerged }: MergeModalProps) {
  const [allGames, setAllGames] = useState<Game[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [filter, setFilter] = useState('');
  const [target, setTarget] = useState<Game | null>(null);
  const [merging, setMerging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listGames('name')
      .then((d) => setAllGames(d.games.filter((g) => g.id !== source.id)))
      .catch(() => setAllGames([]))
      .finally(() => setLoadingList(false));
  }, [source.id]);

  const filtered = allGames.filter((g) =>
    g.name.toLowerCase().includes(filter.toLowerCase())
  );

  const handleMerge = async () => {
    if (!target) return;
    try {
      setMerging(true);
      setError(null);
      const result = await mergeGameInto(source.id, target.id);
      onMerged(result.target_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Merge failed');
      setMerging(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="relative bg-bg-secondary border border-border rounded-lg p-6 w-full max-w-lg shadow-xl">
        <h2 className="text-lg font-semibold text-text-primary mb-2 flex items-center gap-2">
          <GitMerge className="h-5 w-5 text-accent-primary" />
          Merge Into Another Game
        </h2>
        <p className="text-sm text-text-secondary mb-4">
          Pick a target game. All <strong>{source.screenshot_count}</strong> screenshots
          from <strong>{source.name}</strong> will be moved to the target, and{' '}
          <strong>{source.name}</strong> will be deleted. This cannot be undone.
        </p>

        {target ? (
          <div className="space-y-4">
            <div className="bg-bg-primary border border-border rounded-md p-3 text-sm">
              <div className="text-text-muted text-xs mb-1">Target</div>
              <div className="text-text-primary font-medium">{target.name}</div>
              <div className="text-text-muted text-xs mt-1">
                {formatCount(target.screenshot_count ?? 0)}
              </div>
            </div>
            {error && (
              <div className="px-3 py-2 bg-accent-danger/10 border border-accent-danger/30 rounded text-xs text-accent-danger">
                {error}
              </div>
            )}
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setTarget(null)}
                disabled={merging}
                className="px-4 py-2 bg-bg-tertiary text-text-primary rounded-md text-sm hover:bg-bg-tertiary/80 transition-colors disabled:opacity-50"
              >
                Back
              </button>
              <button
                onClick={handleMerge}
                disabled={merging}
                className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-on-accent rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
              >
                {merging && <Loader2 className="h-3 w-3 animate-spin" />}
                Merge {source.screenshot_count} screenshot{source.screenshot_count === 1 ? '' : 's'}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-text-muted" />
              <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter games..."
                className="w-full pl-9 pr-3 py-2 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary"
                autoFocus
              />
            </div>

            <div className="max-h-72 overflow-y-auto bg-bg-primary border border-border rounded-md divide-y divide-border">
              {loadingList ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-text-muted" />
                </div>
              ) : filtered.length === 0 ? (
                <div className="px-3 py-8 text-center text-sm text-text-muted">
                  {allGames.length === 0
                    ? 'No other games to merge into.'
                    : 'No games match the filter.'}
                </div>
              ) : (
                filtered.map((g) => (
                  <button
                    key={g.id}
                    onClick={() => setTarget(g)}
                    className="w-full text-left px-3 py-2 hover:bg-bg-tertiary transition-colors"
                  >
                    <div className="text-sm text-text-primary">{g.name}</div>
                    <div className="text-xs text-text-muted">
                      {formatCount(g.screenshot_count ?? 0)}
                    </div>
                  </button>
                ))
              )}
            </div>

            <div className="flex justify-end">
              <button
                onClick={onClose}
                className="px-4 py-2 bg-bg-tertiary text-text-primary rounded-md text-sm hover:bg-bg-tertiary/80 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
