import { useEffect, useCallback, useState } from 'react';
import { X, ChevronLeft, ChevronRight, Info, Download } from 'lucide-react';
import { galleryImageUrl } from '@/api/gallery';
import { formatDateTime, formatFileSize } from '@/lib/formatters';
import type { Screenshot } from '@/lib/types';

interface GalleryScreenshotViewerProps {
  screenshots: Screenshot[];
  currentIndex: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
}

export function GalleryScreenshotViewer({
  screenshots,
  currentIndex,
  onClose,
  onNavigate,
}: GalleryScreenshotViewerProps) {
  const [showInfo, setShowInfo] = useState(false);

  const screenshot = screenshots[currentIndex];
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex < screenshots.length - 1;

  const handlePrev = useCallback(() => {
    if (hasPrev) onNavigate(currentIndex - 1);
  }, [hasPrev, currentIndex, onNavigate]);

  const handleNext = useCallback(() => {
    if (hasNext) onNavigate(currentIndex + 1);
  }, [hasNext, currentIndex, onNavigate]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
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
    [onClose, handlePrev, handleNext],
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [handleKeyDown]);

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
            onClick={() => setShowInfo(!showInfo)}
            className={`p-2 rounded-md hover:bg-white/10 transition-colors ${
              showInfo ? 'bg-white/10' : ''
            }`}
            title="Toggle info (i)"
          >
            <Info className="h-5 w-5 text-white/70" />
          </button>

          <a
            href={galleryImageUrl(screenshot.id)}
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
            src={galleryImageUrl(screenshot.id)}
            alt={screenshot.filename}
            className="max-w-full max-h-full object-contain select-none"
            draggable={false}
          />
        </div>

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
                {screenshot.steam_description && (
                  <div>
                    <p className="text-text-muted text-xs mb-1">Steam Description</p>
                    <p className="text-text-secondary">{screenshot.steam_description}</p>
                  </div>
                )}
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
