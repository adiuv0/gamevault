export const API_BASE = '/api';

/** What the server accepts for upload (backend `ALLOWED_IMAGE_FORMATS`). */
export const ACCEPTED_IMAGE_TYPES = {
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/png': ['.png'],
  'image/webp': ['.webp'],
  'image/bmp': ['.bmp'],
  'image/tiff': ['.tiff', '.tif'],
  'image/gif': ['.gif'],
  'image/vnd.ms-photo': ['.jxr', '.wdp'],
} as const;
