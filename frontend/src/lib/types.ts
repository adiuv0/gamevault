export interface Game {
  id: number;
  name: string;
  folder_name: string;
  steam_app_id: number | null;
  cover_image_path: string | null;
  developer: string | null;
  publisher: string | null;
  release_date: string | null;
  genres: string | null;
  description: string | null;
  is_public: boolean;
  screenshot_count: number;
  first_screenshot_date: string | null;
  last_screenshot_date: string | null;
  created_at: string;
  updated_at: string;
}

export interface Screenshot {
  id: number;
  game_id: number;
  filename: string;
  file_path: string;
  thumbnail_path_sm: string | null;
  thumbnail_path_md: string | null;
  file_size: number | null;
  width: number | null;
  height: number | null;
  format: string | null;
  taken_at: string | null;
  uploaded_at: string;
  steam_screenshot_id: string | null;
  steam_description: string | null;
  source: 'upload' | 'steam_import' | 'steam_local';
  is_favorite: boolean;
  view_count: number;
  exif_data: string | null;
  has_annotation: boolean;
  created_at: string;
  updated_at: string;
}

export interface Annotation {
  id: number;
  screenshot_id: number;
  content: string;
  content_html: string | null;
  created_at: string;
  updated_at: string;
}

export interface ShareLink {
  id: number;
  screenshot_id: number;
  token: string;
  url: string;
  is_active: boolean;
  expires_at: string | null;
  view_count: number;
  created_at: string;
}

export interface AuthStatus {
  authenticated: boolean;
  setup_required: boolean;
  auth_disabled: boolean;
}

export interface AuthResponse {
  token: string;
  expires_in_days: number;
}

export type ViewMode = 'grid' | 'list';
export type SortOption = 'name' | 'date' | 'count';
