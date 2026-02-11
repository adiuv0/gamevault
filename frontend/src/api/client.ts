import { API_BASE } from '@/lib/constants';

/**
 * Base API client with auth token handling.
 */
class ApiClient {
  private getToken(): string | null {
    return localStorage.getItem('gamevault_token');
  }

  private headers(): HeadersInit {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    const token = this.getToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    return headers;
  }

  async get<T>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: this.headers(),
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, error.detail || 'Request failed');
    }
    return res.json();
  }

  async post<T>(path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: this.headers(),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, error.detail || 'Request failed');
    }
    return res.json();
  }

  async put<T>(path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'PUT',
      headers: this.headers(),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, error.detail || 'Request failed');
    }
    return res.json();
  }

  async delete<T>(path: string): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'DELETE',
      headers: this.headers(),
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, error.detail || 'Request failed');
    }
    return res.json();
  }

  /**
   * Upload files with progress tracking via multipart form data.
   * Returns a task ID for SSE progress tracking.
   */
  async uploadFiles(
    path: string,
    files: File[],
    fields: Record<string, string>,
    onProgress?: (loaded: number, total: number) => void,
  ): Promise<{ task_id: string; file_count: number }> {
    const formData = new FormData();
    for (const file of files) {
      formData.append('files', file);
    }
    for (const [key, value] of Object.entries(fields)) {
      formData.append(key, value);
    }

    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${API_BASE}${path}`);

      const token = this.getToken();
      if (token) {
        xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      }

      if (onProgress) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            onProgress(e.loaded, e.total);
          }
        };
      }

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          reject(new ApiError(xhr.status, 'Upload failed'));
        }
      };

      xhr.onerror = () => reject(new ApiError(0, 'Network error'));
      xhr.send(formData);
    });
  }

  /**
   * Connect to an SSE endpoint for progress tracking.
   */
  connectSSE(path: string, onEvent: (data: unknown) => void, onError?: (e: Event) => void): EventSource {
    const token = this.getToken();
    const url = token
      ? `${API_BASE}${path}?token=${encodeURIComponent(token)}`
      : `${API_BASE}${path}`;

    const es = new EventSource(url);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        onEvent(data);
      } catch {
        onEvent(e.data);
      }
    };
    if (onError) {
      es.onerror = onError;
    }
    return es;
  }
}

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export const api = new ApiClient();
