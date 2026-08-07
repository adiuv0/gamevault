import { useEffect, useCallback, useMemo, useState } from 'react';
import { X, ChevronLeft, ChevronRight, Info, Download, Repeat2 } from 'lucide-react';
import { galleryImageUrl, galleryPreviewUrl } from '@/api/gallery';
import { formatDateTime, formatFileSize } from '@/lib/formatters';
import type { Screenshot, ScreenshotVariant } from '@/lib/types';

function getVariants(screenshot: Screenshot | undefined): ScreenshotVariant[] {
  if (!screenshot) return [];
  return screenshot.variants?.length ? screenshot.variants : [screenshot];
}

function variantLabel(variant: ScreenshotVariant, index: number, variants: ScreenshotVariant[]): string {
  const format = variant.format?.toUpperCase() || 'FILE';
  const sameFormatCount = variants.filter((item) => item.format === variant.format).length;
  return sameFormatCount > 1 ? `${format} ${index + 1}` : format;
}

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
  const [activeVariantId, setActiveVariantId] = useState<number | null>(null);

  const screenshot = screenshots[currentIndex];
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex < screenshots.length - 1;
  const variants = useMemo(() => getVariants(screenshot), [screenshot]);
  const activeVariant = variants.find((variant) => variant.id === activeVariantId)
    || variants[0]
    || screenshot;

  const handleCycleVariant = useCallback(() => {
    if (variants.length < 2 || !activeVariant) return;
    const currentVariantIndex = variants.findIndex((variant) => variant.id === activeVariant.id);
    setActiveVariantId(variants[(currentVariantIndex + 1) % variants.length].id);
  }, [activeVariant, variants]);

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
        case 'h':
          handleCycleVariant();
          break;
      }
    },
    [onClose, handlePrev, handleNext, handleCycleVariant],
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [handleKeyDown]);

  if (!screenshot || !activeVariant) return null;

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
        </div>

        <div className="flex flex-wrap items-center justify-end gap-1 sm:gap-2">
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
            onClick={() => setShowInfo(!showInfo)}
            className={`p-2 rounded-md hover:bg-white/10 transition-colors ${
              showInfo ? 'bg-white/10' : ''
            }`}
            title="Toggle info (i)"
          >
            <Info className="h-5 w-5 text-white/70" />
          </button>

          <a
            href={galleryImageUrl(activeVariant.id)}
            download={activeVariant.filename}
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
        <div className={`flex-1 flex items-center justify-center p-4 ${showInfo ? 'sm:mr-80' : ''}`}>
          <img
            key={activeVariant.id}
            src={galleryPreviewUrl(activeVariant.id)}
            alt={activeVariant.filename}
            className="max-w-full max-h-full object-contain select-none"
            draggable={false}
          />
        </div>

        {/* Info panel */}
        {showInfo && (
          <div className="absolute right-0 top-0 bottom-0 w-full sm:w-80 bg-bg-secondary/95 backdrop-blur border-l border-border overflow-y-auto">
            <div className="p-4 space-y-4">
              <h3 className="text-sm font-medium text-text-primary">Details</h3>

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
