import { useEffect } from 'react';

/** Warm the browser cache for images the user is likely to open next. */
export function usePreloadImages(urls: (string | null | undefined)[]) {
  const key = urls.filter(Boolean).join('\n');
  useEffect(() => {
    if (!key) return;
    // Previews are small; let in-flight ones finish so they are cached if the
    // user navigates there a moment later.
    for (const url of key.split('\n')) {
      const image = new Image();
      image.decoding = 'async';
      image.src = url;
    }
  }, [key]);
}
