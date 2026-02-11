/**
 * Format a date string for display.
 */
export function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Unknown date';
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

/**
 * Format a date with time.
 */
export function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return 'Unknown date';
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/**
 * Format file size in bytes to human readable.
 */
export function formatFileSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return 'Unknown size';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/**
 * Format a screenshot count.
 */
export function formatCount(count: number): string {
  if (count === 0) return 'No screenshots';
  if (count === 1) return '1 screenshot';
  return `${count.toLocaleString()} screenshots`;
}

/**
 * Get a relative time string (e.g., "2 days ago").
 */
export function timeAgo(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const seconds = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return formatDate(dateStr);
}
