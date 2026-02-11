import { useEffect, useCallback, useState } from 'react';
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
} from 'lucide-react';
import { getScreenshotImageUrl } from '@/api/screenshots';
import { toggleFavorite, createShareLink, getShareLink, deleteShareLink } from '@/api/screenshots';
import { AnnotationEditor } from './AnnotationEditor';
import { formatDateTime, formatFileSize } from '@/lib/formatters';
import type { Screenshot, ShareLink } from '@/lib/types';

interface ScreenshotViewerProps {
  screenshots: Screenshot[];
  currentIndex: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
  onFavoriteToggle?: (id: number, isFav: boolean) => void;
  onAnnotationChanged?: (id: number, hasAnnotation: boolean) => void;
}

export function ScreenshotViewer({
  screenshots,
  currentIndex,
  onClose,
  onNavigate,
  onFavoriteToggle,
  onAnnotationChanged,
}: ScreenshotViewerProps) {
  const [showInfo, setShowInfo] = useState(false);
  const [togglingFav, setTogglingFav] = useState(false);

  // Share state
  const [showSharePanel, setShowSharePanel] = useState(false);
  const [shareLink, setShareLink] = useState<ShareLink | null>(null);
  const [shareLoading, setShareLoading] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);
  const [shareDeleting, setShareDeleting] = useState(false);

  const screenshot = screenshots[currentIndex];
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex < screenshots.length - 1;

  // Reset share state when screenshot changes
  useEffect(() => {
    setShowSharePanel(false);
    setShareLink(null);
    setShareCopied(false);
  }, [currentIndex]);

  const handlePrev = useCallback(() => {
    if (hasPrev) onNavigate(currentIndex - 1);
  }, [hasPrev, currentIndex, onNavigate]);

  const handleNext = useCallback(() => {
    if (hasNext) onNavigate(currentIndex + 1);
  }, [hasNext, currentIndex, onNavigate]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
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
      }
    },
    [onClose, handlePrev, handleNext, showSharePanel],
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
    if (togglingFav) return;
    try {
      setTogglingFav(true);
      const result = await toggleFavorite(screenshot.id);
      onFavoriteToggle?.(screenshot.id, result.is_favorite);
    } catch {
      // ignore
    } finally {
      setTogglingFav(false);
    }
  };

  const handleShareToggle = async () => {
    if (showSharePanel) {
      setShowSharePanel(false);
      return;
    }

    setShowSharePanel(true);
    setShareLoading(true);

    try {
      // Try to get existing share link first
      const existing = await getShareLink(screenshot.id);
      if (existing) {
        setShareLink(existing);
      }
    } catch {
      // No existing link, that's fine
    } finally {
      setShareLoading(false);
    }
  };

  const handleCreateShareLink = async () => {
    try {
      setShareLoading(true);
      const link = await createShareLink(screenshot.id);
      setShareLink(link);
    } catch {
      // ignore
    } finally {
      setShareLoading(false);
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
    try {
      setShareDeleting(true);
      await deleteShareLink(screenshot.id);
      setShareLink(null);
    } catch {
      // ignore
    } finally {
      setShareDeleting(false);
    }
  };

  if (!screenshot) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/95 flex flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-3 bg-black/50">
        <div className="flex items-center gap-4">
          <span className="text-sm text-text-secondary">
            {currentIndex + 1} / {screenshots.length}
          </span>
          <span className="text-sm text-text-muted truncate max-w-xs">
            {screenshot.filename}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleFavoriteToggle}
            className="p-2 rounded-md hover:bg-white/10 transition-colors"
            title="Toggle favorite"
          >
            <Heart
              className={`h-5 w-5 ${
                screenshot.is_favorite
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
            href={getScreenshotImageUrl(screenshot.id)}
            download={screenshot.filename}
            className="p-2 rounded-md hover:bg-white/10 transition-colors"
            title="Download"
          >
            <Download className="h-5 w-5 text-white/70" />
          </a>

          <button
            onClick={onClose}
            className="p-2 rounded-md hover:bg-white/10 transition-colors"
            title="Close (Esc)"
          >
            <X className="h-5 w-5 text-white/70" />
          </button>
        </div>
      </div>

      {/* Main area */}
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
        <div className={`flex-1 flex items-center justify-center p-4 ${showInfo ? 'mr-80' : ''}`}>
          <img
            key={screenshot.id}
            src={getScreenshotImageUrl(screenshot.id)}
            alt={screenshot.filename}
            className="max-w-full max-h-full object-contain select-none"
            draggable={false}
          />
        </div>

        {/* Share popup */}
        {showSharePanel && (
          <div className="absolute top-2 right-4 z-20 w-80 bg-bg-secondary/95 backdrop-blur border border-border rounded-lg shadow-xl">
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
                    className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
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
          <div className="absolute right-0 top-0 bottom-0 w-80 bg-bg-secondary/95 backdrop-blur border-l border-border overflow-y-auto">
            <div className="p-4 space-y-4">
              <h3 className="text-sm font-medium text-text-primary">Details</h3>

              <div className="space-y-3 text-sm">
                <InfoRow label="Filename" value={screenshot.filename} />
                <InfoRow
                  label="Date"
                  value={formatDateTime(screenshot.taken_at || screenshot.uploaded_at)}
                />
                {screenshot.width && screenshot.height && (
                  <InfoRow
                    label="Dimensions"
                    value={`${screenshot.width} x ${screenshot.height}`}
                  />
                )}
                <InfoRow
                  label="Size"
                  value={formatFileSize(screenshot.file_size)}
                />
                {screenshot.format && (
                  <InfoRow label="Format" value={screenshot.format.toUpperCase()} />
                )}
                <InfoRow label="Source" value={
                  screenshot.source === 'steam_import' ? 'Steam Import'
                  : screenshot.source === 'steam_local' ? 'Steam Local'
                  : 'Upload'
                } />
                {screenshot.steam_description && (
                  <div>
                    <p className="text-text-muted text-xs mb-1">Steam Description</p>
                    <p className="text-text-secondary">{screenshot.steam_description}</p>
                  </div>
                )}
              </div>

              <div className="pt-3 border-t border-border">
                <AnnotationEditor
                  screenshotId={screenshot.id}
                  hasAnnotation={!!screenshot.has_annotation}
                  onAnnotationChanged={(has) => onAnnotationChanged?.(screenshot.id, has)}
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
