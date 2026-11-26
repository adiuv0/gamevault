import { useEffect, useCallback, useMemo, useRef, useState } from 'react';
import {
  X,
  ChevronLeft,
  ChevronRight,
  Heart,
  Download,
  Info,
  Share2,
  Link2,
  Check,
  Loader2,
  Trash2,
  Repeat2,
  Unlink,
} from 'lucide-react';
import {
  getScreenshot,
  ungroupScreenshot,
  getScreenshotFullUrl,
  getScreenshotImageUrl,
  getScreenshotPreviewUrl,
  deleteScreenshot,
  type DeleteResult,
} from '@/api/screenshots';
import { toggleFavorite, createShareLink, getShareLink, deleteShareLink } from '@/api/screenshots';
import { AnnotationEditor } from './AnnotationEditor';
import { ProgressiveImage } from '@/components/shared/ProgressiveImage';
import { ConfirmDialog } from '@/components/shared/ConfirmDialog';
import { usePreloadImages } from '@/hooks/usePreloadImages';
import { formatDateTime, formatFileSize } from '@/lib/formatters';
import type {
  Screenshot,
  ScreenshotListItem,
  ScreenshotVariant,
  ShareLink,
} from '@/lib/types';


function getVariants(
  screenshot: Screenshot | undefined,
  listItem: ScreenshotListItem | undefined,
): ScreenshotVariant[] {
  if (screenshot) return screenshot.variants.length ? screenshot.variants : [screenshot];
  if (!listItem) return [];
  return [{
    id: listItem.id,
    filename: listItem.filename,
    media_version: listItem.media_version,
    file_size: null,
    width: null,
    height: null,
    format: null,
    taken_at: listItem.taken_at,
    uploaded_at: listItem.uploaded_at,
  }];
}

function variantLabel(variant: ScreenshotVariant, index: number, variants: ScreenshotVariant[]): string {
  const format = variant.format?.toUpperCase() || 'FILE';
  const sameFormatCount = variants.filter((item) => item.format === variant.format).length;
  return sameFormatCount > 1 ? `${format} ${index + 1}` : format;
}

interface ScreenshotViewerProps {
  screenshots: ScreenshotListItem[];
  currentIndex: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
  onFavoriteToggle?: (id: number, isFav: boolean) => void;
  onAnnotationChanged?: (id: number, hasAnnotation: boolean) => void;
  onGroupingChanged?: () => void;
  /** Called after a delete. `result.deleted_captures` is 0 when only one file of the capture went. */
  onDeleted?: (id: number, result: DeleteResult) => void;
}

export function ScreenshotViewer({
  screenshots,
  currentIndex,
  onClose,
  onNavigate,
  onFavoriteToggle,
  onAnnotationChanged,
  onGroupingChanged,
  onDeleted,
}: ScreenshotViewerProps) {
  const [showInfo, setShowInfo] = useState(false);
  const [ungrouping, setUngrouping] = useState(false);
  const [groupError, setGroupError] = useState<string | null>(null);
  const [togglingFav, setTogglingFav] = useState(false);
  const [activeVariantId, setActiveVariantId] = useState<number | null>(null);
  const [detailsById, setDetailsById] = useState<Record<number, Screenshot>>({});
  const [detailErrorId, setDetailErrorId] = useState<number | null>(null);
  const [originalFailures, setOriginalFailures] = useState<Record<number, boolean>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Share state
  const [showSharePanel, setShowSharePanel] = useState(false);
  const [shareLink, setShareLink] = useState<ShareLink | null>(null);
  const [shareLoading, setShareLoading] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);
  const [shareDeleting, setShareDeleting] = useState(false);

  const listItem = screenshots[currentIndex];
  const screenshot = listItem ? detailsById[listItem.id] : undefined;
  const detailError = detailErrorId === listItem?.id;
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex < screenshots.length - 1;
  const variants = useMemo(
    () => getVariants(screenshot, listItem),
    [screenshot, listItem],
  );
  const activeVariant = variants.find((variant) => variant.id === activeVariantId)
    || variants[0]
    || listItem;
  // Browsers can't decode these; the server renders them to a full-size JPEG.
  const needsRender = ['jxr', 'wdp', 'tif', 'tiff'].includes(
    activeVariant?.format?.toLowerCase() || '',
  ) || /\.(jxr|wdp|tiff?)$/i.test(activeVariant?.filename || '');
  const usePreview = !!activeVariant && !!originalFailures[activeVariant.id];

  const prevItem = screenshots[currentIndex - 1];
  const nextItem = screenshots[currentIndex + 1];
  usePreloadImages([
    nextItem && getScreenshotPreviewUrl(nextItem.id, nextItem.media_version),
    prevItem && getScreenshotPreviewUrl(prevItem.id, prevItem.media_version),
  ]);

  useEffect(() => {
    if (!listItem || detailsById[listItem.id]) return;
    let cancelled = false;
    getScreenshot(listItem.id)
      .then((details) => {
        if (!cancelled) {
          setDetailsById((current) => ({ ...current, [listItem.id]: details }));
          setDetailErrorId(null);
        }
      })
      .catch(() => {
        if (!cancelled) setDetailErrorId(listItem.id);
      });
    return () => {
      cancelled = true;
    };
  }, [detailsById, listItem]);

  // Wait for the capture's file list, so the dialog can say what a delete removes.
  const canDelete = !!screenshot || detailError;

  const handleCycleVariant = useCallback(() => {
    if (variants.length < 2 || !activeVariant) return;
    const currentVariantIndex = variants.findIndex((variant) => variant.id === activeVariant.id);
    setActiveVariantId(variants[(currentVariantIndex + 1) % variants.length].id);
  }, [activeVariant, variants]);

  // Bumped whenever the screenshot changes so a slow share request can't attach
  // its link to the screenshot the user navigated to.
  const shareRequest = useRef(0);

  // Reset share state when screenshot changes
  useEffect(() => {
    shareRequest.current += 1;
    setShowSharePanel(false);
    setShareLoading(false);
    setShareLink(null);
    setShareCopied(false);
    setConfirmDelete(false);
    setDeleteError(null);
  }, [listItem?.id]);

  const handlePrev = useCallback(() => {
    if (hasPrev) onNavigate(currentIndex - 1);
  }, [hasPrev, currentIndex, onNavigate]);

  const handleNext = useCallback(() => {
    if (hasNext) onNavigate(currentIndex + 1);
  }, [hasNext, currentIndex, onNavigate]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const target = e.target;
      if (e.defaultPrevented || e.isComposing || e.ctrlKey || e.metaKey || e.altKey
        || (target instanceof HTMLElement && target.closest('input, textarea, select, [contenteditable="true"]'))) return;
      // The delete dialog handles its own keys.
      if (confirmDelete) return;
      // Don't handle keyboard shortcuts if share panel is open (for text selection)
      if (showSharePanel) {
        if (e.key === 'Escape') {
          setShowSharePanel(false);
          return;
        }
        return;
      }
      switch (e.key) {
        case 'Escape':
          onClose();
          break;
        case 'ArrowLeft':
          handlePrev();
          break;
        case 'ArrowRight':
          handleNext();
          break;
        case 'i':
          setShowInfo((prev) => !prev);
          break;
        case 'h':
          handleCycleVariant();
          break;
        case 'Delete':
          if (onDeleted && canDelete) setConfirmDelete(true);
          break;
      }
    },
    [onClose, handlePrev, handleNext, handleCycleVariant, showSharePanel, confirmDelete, onDeleted, canDelete],
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [handleKeyDown]);

  const handleFavoriteToggle = async () => {
    if (togglingFav || !listItem) return;
    try {
      setTogglingFav(true);
      const result = await toggleFavorite(listItem.id);
      onFavoriteToggle?.(listItem.id, result.is_favorite);
    } catch {
      // ignore
    } finally {
      setTogglingFav(false);
    }
  };

  const handleShareToggle = async () => {
    if (!listItem) return;
    if (showSharePanel) {
      setShowSharePanel(false);
      return;
    }

    setShowSharePanel(true);
    setShareLoading(true);
    const request = shareRequest.current;

    try {
      // Try to get existing share link first
      const existing = await getShareLink(listItem.id);
      if (existing && request === shareRequest.current) {
        setShareLink(existing);
      }
    } catch {
      // No existing link, that's fine
    } finally {
      if (request === shareRequest.current) setShareLoading(false);
    }
  };

  const handleCreateShareLink = async () => {
    if (!listItem) return;
    const request = shareRequest.current;
    try {
      setShareLoading(true);
      const link = await createShareLink(listItem.id);
      if (request === shareRequest.current) setShareLink(link);
    } catch {
      // ignore
    } finally {
      if (request === shareRequest.current) setShareLoading(false);
    }
  };

  const handleCopyShareLink = async () => {
    if (!shareLink) return;
    try {
      await navigator.clipboard.writeText(shareLink.url);
      setShareCopied(true);
      setTimeout(() => setShareCopied(false), 2000);
    } catch {
      // Fallback for non-https contexts
      const textArea = document.createElement('textarea');
      textArea.value = shareLink.url;
      document.body.appendChild(textArea);
      textArea.select();
      document.execCommand('copy');
      document.body.removeChild(textArea);
      setShareCopied(true);
      setTimeout(() => setShareCopied(false), 2000);
    }
  };

  const handleDeleteShareLink = async () => {
    if (!listItem) return;
    try {
      setShareDeleting(true);
      await deleteShareLink(listItem.id);
      setShareLink(null);
    } catch {
      // ignore
    } finally {
      setShareDeleting(false);
    }
  };

  const handleDelete = async (onlyThisFile: boolean) => {
    if (!listItem || !activeVariant || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const result = await deleteScreenshot(onlyThisFile ? activeVariant.id : listItem.id, onlyThisFile);
      setConfirmDelete(false);
      setActiveVariantId(null);
      // The capture's files changed: fetch its details again when it's shown.
      setDetailsById((current) => {
        const next = { ...current };
        delete next[listItem.id];
        return next;
      });
      onDeleted?.(listItem.id, result);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : 'Could not delete the screenshot');
    } finally {
      setDeleting(false);
    }
  };

  if (!listItem || !activeVariant) return null;

  const fileCount = variants.length;
  const activeLabel = variantLabel(activeVariant, variants.indexOf(activeVariant), variants);

  return (
    <div className="fixed inset-0 z-50 bg-black/95 flex flex-col">
      {/* Top bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 sm:px-4 py-3 bg-black/50">
        <div className="min-w-0 flex items-center gap-3 sm:gap-4">
          <span className="text-sm text-text-secondary">
            {currentIndex + 1} / {screenshots.length}
          </span>
          <span className="text-sm text-text-muted truncate max-w-[10rem] sm:max-w-xs">
            {activeVariant.filename}
          </span>
          {!screenshot && !detailError && (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-white/40" />
          )}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-1 sm:gap-2">
          {variants.length > 1 && (
            <button title="Ungroup selected variant" disabled={ungrouping}
              className="p-2 rounded-md hover:bg-white/10 disabled:opacity-50"
              onClick={async () => {
                setUngrouping(true);
                setGroupError(null);
                try {
                  await ungroupScreenshot(activeVariant.id);
                  setDetailsById({});
                  onGroupingChanged?.();
                } catch (error) {
                  setGroupError(error instanceof Error ? error.message : 'Could not ungroup variant');
                } finally { setUngrouping(false); }
              }}>
              {ungrouping ? <Loader2 className="h-5 w-5 animate-spin" /> : <Unlink className="h-5 w-5" />}
            </button>
          )}
          {variants.length > 1 && (
            <div className="flex items-center rounded-md bg-white/5 p-0.5" title="Switch file variant (h)">
              <Repeat2 className="h-4 w-4 text-white/50 mx-1.5" />
              {variants.map((variant, index) => (
                <button
                  key={variant.id}
                  type="button"
                  onClick={() => setActiveVariantId(variant.id)}
                  className={`px-2 py-1 rounded text-xs transition-colors ${
                    activeVariant.id === variant.id
                      ? 'bg-white/15 text-white'
                      : 'text-white/60 hover:text-white'
                  }`}
                >
                  {variantLabel(variant, index, variants)}
                </button>
              ))}
            </div>
          )}

          <button
            onClick={handleFavoriteToggle}
            className="p-2 rounded-md hover:bg-white/10 transition-colors"
            title="Toggle favorite"
          >
            <Heart
              className={`h-5 w-5 ${
                listItem.is_favorite
                  ? 'text-red-400 fill-red-400'
                  : 'text-white/70'
              }`}
            />
          </button>

          <button
            onClick={handleShareToggle}
            className={`p-2 rounded-md hover:bg-white/10 transition-colors ${
              showSharePanel ? 'bg-white/10' : ''
            }`}
            title="Share"
          >
            <Share2 className="h-5 w-5 text-white/70" />
          </button>

          <button
            onClick={() => setShowInfo(!showInfo)}
            className={`p-2 rounded-md hover:bg-white/10 transition-colors ${
              showInfo ? 'bg-white/10' : ''
            }`}
            title="Toggle info (i)"
          >
            <Info className="h-5 w-5 text-white/70" />
          </button>

          <a
            href={getScreenshotImageUrl(activeVariant.id, activeVariant.media_version)}
            download={activeVariant.filename}
            className="p-2 rounded-md hover:bg-white/10 transition-colors"
            title="Download"
          >
            <Download className="h-5 w-5 text-white/70" />
          </a>

          {onDeleted && (
            <button
              onClick={() => { setDeleteError(null); setConfirmDelete(true); }}
              disabled={!canDelete}
              className="p-2 rounded-md text-white/70 hover:text-accent-danger hover:bg-accent-danger/15 transition-colors disabled:opacity-40"
              title="Delete (Del)"
              aria-label="Delete screenshot"
            >
              <Trash2 className="h-5 w-5" />
            </button>
          )}

          <button
            onClick={onClose}
            className="p-2 rounded-md hover:bg-white/10 transition-colors"
            title="Close (Esc)"
          >
            <X className="h-5 w-5 text-white/70" />
          </button>
        </div>
      </div>

      {confirmDelete && (
        <ConfirmDialog
          title={fileCount > 1 ? 'Delete this capture?' : 'Delete this screenshot?'}
          busy={deleting}
          error={deleteError}
          onCancel={() => setConfirmDelete(false)}
          actions={fileCount > 1
            ? [
                { label: `Only the ${activeLabel} file`, onClick: () => void handleDelete(true) },
                { label: `Delete all ${fileCount} files`, onClick: () => void handleDelete(false), danger: true },
              ]
            : [{ label: 'Delete', onClick: () => void handleDelete(false), danger: true }]}
        >
          {fileCount > 1 ? (
            <>
              <p>
                This capture has {fileCount} files: {variants.map((variant, index) => variantLabel(variant, index, variants)).join(', ')}.
                Delete all of them, or only the {activeLabel} file you're looking at?
              </p>
              <p>The files are removed from GameVault and from the library folder. This can't be undone.</p>
            </>
          ) : (
            <p>
              <strong className="text-text-primary break-all">{activeVariant.filename}</strong> is removed from GameVault
              and from the library folder. This can't be undone.
            </p>
          )}
        </ConfirmDialog>
      )}

      {/* Main area */}
      {groupError && <p role="alert" className="px-4 text-sm text-accent-danger">{groupError}</p>}
      <div className="flex-1 flex relative overflow-hidden">
        {/* Navigation arrows */}
        {hasPrev && (
          <button
            onClick={handlePrev}
            className="absolute left-2 top-1/2 -translate-y-1/2 z-10 p-3 rounded-full bg-black/50 hover:bg-black/70 transition-colors"
          >
            <ChevronLeft className="h-6 w-6 text-white" />
          </button>
        )}
        {hasNext && (
          <button
            onClick={handleNext}
            className="absolute right-2 top-1/2 -translate-y-1/2 z-10 p-3 rounded-full bg-black/50 hover:bg-black/70 transition-colors"
          >
            <ChevronRight className="h-6 w-6 text-white" />
          </button>
        )}

        {/* Image */}
        <div className={`flex-1 flex items-center justify-center p-4 ${showInfo ? 'sm:mr-80' : ''}`}>
          <ProgressiveImage
            key={activeVariant.id}
            previewUrl={getScreenshotPreviewUrl(activeVariant.id, activeVariant.media_version)}
            originalUrl={usePreview
              ? null
              : (needsRender ? getScreenshotFullUrl : getScreenshotImageUrl)(activeVariant.id, activeVariant.media_version)}
            alt={activeVariant.filename}
            className="max-w-full max-h-full object-contain select-none"
            onOriginalError={() => {
              setOriginalFailures((current) => ({
                ...current,
                [activeVariant.id]: true,
              }));
            }}
          />
        </div>

        {/* Share popup */}
        {showSharePanel && (
          <div className="absolute top-2 right-4 z-20 w-80 max-w-[calc(100%-2rem)] bg-bg-secondary/95 backdrop-blur border border-border rounded-lg shadow-xl">
            <div className="p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-text-primary flex items-center gap-2">
                  <Share2 className="h-4 w-4" />
                  Share Screenshot
                </h3>
                <button
                  onClick={() => setShowSharePanel(false)}
                  className="p-1 rounded hover:bg-white/10 transition-colors"
                >
                  <X className="h-3.5 w-3.5 text-text-muted" />
                </button>
              </div>

              {shareLoading && !shareLink ? (
                <div className="flex items-center justify-center py-4">
                  <Loader2 className="h-5 w-5 animate-spin text-text-muted" />
                </div>
              ) : shareLink ? (
                <>
                  {/* Share link URL */}
                  <div className="flex items-center gap-2">
                    <div className="flex-1 bg-bg-primary border border-border rounded-md px-3 py-2 text-xs text-text-secondary font-mono truncate">
                      {shareLink.url}
                    </div>
                    <button
                      onClick={handleCopyShareLink}
                      className={`p-2 rounded-md transition-colors flex-shrink-0 ${
                        shareCopied
                          ? 'bg-accent-success/20 text-accent-success'
                          : 'bg-bg-tertiary hover:bg-bg-tertiary/80 text-text-primary'
                      }`}
                      title={shareCopied ? 'Copied!' : 'Copy link'}
                    >
                      {shareCopied ? (
                        <Check className="h-4 w-4" />
                      ) : (
                        <Link2 className="h-4 w-4" />
                      )}
                    </button>
                  </div>

                  {/* Link stats */}
                  <div className="flex items-center justify-between text-xs text-text-muted">
                    <span>
                      {shareLink.view_count} view{shareLink.view_count !== 1 ? 's' : ''}
                    </span>
                    {shareLink.expires_at ? (
                      <span>Expires: {new Date(shareLink.expires_at).toLocaleDateString()}</span>
                    ) : (
                      <span>No expiration</span>
                    )}
                  </div>

                  {/* Deactivate button */}
                  <button
                    onClick={handleDeleteShareLink}
                    disabled={shareDeleting}
                    className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs text-accent-danger border border-accent-danger/30 rounded-md hover:bg-accent-danger/10 transition-colors disabled:opacity-50"
                  >
                    {shareDeleting ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Trash2 className="h-3 w-3" />
                    )}
                    Deactivate Link
                  </button>
                </>
              ) : (
                <>
                  <p className="text-xs text-text-secondary">
                    Create a public link to share this screenshot. Anyone with the link can view it.
                  </p>
                  <button
                    onClick={handleCreateShareLink}
                    disabled={shareLoading}
                    className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-accent-primary text-on-accent rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
                  >
                    {shareLoading ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Link2 className="h-4 w-4" />
                    )}
                    Create Share Link
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {/* Info panel */}
        {showInfo && (
          <div className="absolute right-0 top-0 bottom-0 w-full sm:w-80 bg-bg-secondary/95 backdrop-blur border-l border-border overflow-y-auto">
            <div className="p-4 space-y-4">
              <h3 className="text-sm font-medium text-text-primary">Details</h3>

              {!screenshot ? (
                <p className="text-sm text-text-muted">
                  {detailError ? 'Details are temporarily unavailable.' : 'Loading details...'}
                </p>
              ) : (
                <div className="space-y-3 text-sm">
                  <InfoRow label="Filename" value={activeVariant.filename} />
                  <InfoRow
                    label="Date"
                    value={formatDateTime(activeVariant.taken_at || activeVariant.uploaded_at)}
                  />
                  {activeVariant.width && activeVariant.height && (
                    <InfoRow
                      label="Dimensions"
                      value={`${activeVariant.width} x ${activeVariant.height}`}
                    />
                  )}
                  <InfoRow
                    label="Size"
                    value={formatFileSize(activeVariant.file_size)}
                  />
                  {activeVariant.format && (
                    <InfoRow label="Format" value={activeVariant.format.toUpperCase()} />
                  )}
                  <InfoRow label="Source" value={
                    activeVariant.source === 'steam_import' ? 'Steam Import'
                    : activeVariant.source === 'steam_local' ? 'Steam Local'
                    : activeVariant.source === 'specialk_import' ? 'Special K Import'
                    : 'Upload'
                  } />
                  {screenshot.steam_description && (
                    <div>
                      <p className="text-text-muted text-xs mb-1">Steam Description</p>
                      <p className="text-text-secondary">{screenshot.steam_description}</p>
                    </div>
                  )}
                </div>
              )}

              <div className="pt-3 border-t border-border">
                <AnnotationEditor
                  key={listItem.id}
                  screenshotId={listItem.id}
                  hasAnnotation={!!listItem.has_annotation}
                  onAnnotationChanged={(has) => onAnnotationChanged?.(listItem.id, has)}
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-text-muted text-xs mb-0.5">{label}</p>
      <p className="text-text-secondary break-all">{value}</p>
    </div>
  );
}
