import { useEffect, useRef, useState } from 'react';

interface ProgressiveImageProps {
  previewUrl: string;
  /** Full-resolution source; null shows only the preview (HDR, TIFF, or a failed original). */
  originalUrl: string | null;
  alt: string;
  className?: string;
  onOriginalError?: () => void;
}

/**
 * Show the small cached preview at once, then swap in the original once it has
 * downloaded and decoded off-screen. Multi-megabyte PNG originals otherwise leave
 * the viewer blank for seconds on a slow link.
 */
export function ProgressiveImage({
  previewUrl,
  originalUrl,
  alt,
  className,
  onOriginalError,
}: ProgressiveImageProps) {
  const [loadedOriginal, setLoadedOriginal] = useState<string | null>(null);
  const onErrorRef = useRef(onOriginalError);

  useEffect(() => {
    onErrorRef.current = onOriginalError;
  }, [onOriginalError]);

  useEffect(() => {
    if (!originalUrl) return;
    let cancelled = false;
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => {
      // decode() can reject for very large images; the browser still paints them.
      image.decode().catch(() => undefined).then(() => {
        if (!cancelled) setLoadedOriginal(originalUrl);
      });
    };
    image.onerror = () => {
      if (!cancelled) onErrorRef.current?.();
    };
    image.src = originalUrl;
    return () => {
      cancelled = true;
      image.onload = null;
      image.onerror = null;
      // Abort the download when the user has already moved on.
      image.src = '';
    };
  }, [originalUrl]);

  const src = originalUrl && loadedOriginal === originalUrl ? originalUrl : previewUrl;

  return (
    <img
      src={src}
      alt={alt}
      className={className}
      draggable={false}
      decoding="async"
    />
  );
}
