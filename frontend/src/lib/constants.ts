export const API_BASE = '/api';

export const THUMBNAIL_SIZES = {
  SM: 'sm',
  MD: 'md',
} as const;

export const VIEW_MODES = {
  GRID: 'grid',
  LIST: 'list',
} as const;

export const SORT_OPTIONS = {
  NAME: 'name',
  DATE: 'date',
  COUNT: 'count',
  RELEVANCE: 'relevance',
} as const;

export const ACCEPTED_IMAGE_TYPES = {
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/png': ['.png'],
  'image/webp': ['.webp'],
  'image/bmp': ['.bmp'],
  'image/tiff': ['.tiff', '.tif'],
} as const;

export const NAV_ITEMS = [
  { path: '/', label: 'Library', icon: 'Gamepad2' },
  { path: '/timeline', label: 'Timeline', icon: 'Clock' },
  { path: '/upload', label: 'Upload', icon: 'Upload' },
  { path: '/import/steam', label: 'Steam Import', icon: 'Download' },
  { path: '/search', label: 'Search', icon: 'Search' },
  { path: '/settings', label: 'Settings', icon: 'Settings' },
] as const;
