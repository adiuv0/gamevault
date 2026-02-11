/**
 * Check if a file is an accepted image type.
 */
export function isAcceptedImage(file: File): boolean {
  const accepted = [
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/bmp',
    'image/tiff',
  ];
  return accepted.includes(file.type);
}

/**
 * Validate a password meets minimum requirements.
 */
export function isValidPassword(password: string): boolean {
  return password.length >= 6;
}

/**
 * Check if a string looks like a Steam user ID.
 */
export function isValidSteamUserId(id: string): boolean {
  return id.trim().length > 0;
}
