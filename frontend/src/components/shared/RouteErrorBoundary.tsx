import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { EmptyState } from './EmptyState';

const RELOAD_KEY = 'gamevault_chunk_reload_at';

/** A page chunk that no longer exists: the container was updated under an open tab. */
function isStaleChunkError(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name} ${error.message}` : String(error);
  return /dynamically imported module|module script failed|ChunkLoadError|Loading chunk/i.test(message);
}

function reloadedRecently(): boolean {
  try {
    const at = Number(sessionStorage.getItem(RELOAD_KEY) || 0);
    return Date.now() - at < 10_000;
  } catch {
    return false;
  }
}

interface Props {
  children: ReactNode;
}

interface State {
  error: unknown;
}

/**
 * Keeps one failing page from blanking the whole app. Key it by pathname so
 * navigating away clears the error.
 */
export class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error('Page failed to render', error, info.componentStack);
    // After an update the old page files are gone; a reload fetches the new
    // ones. Only once, so a real outage shows the message instead of looping.
    if (isStaleChunkError(error) && !reloadedRecently()) {
      try {
        sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
      } catch {
        // Private mode: still reload once; the message shows if it fails again.
      }
      window.location.reload();
    }
  }

  render() {
    if (!this.state.error) return this.props.children;
    const stale = isStaleChunkError(this.state.error);
    return (
      <EmptyState
        icon={<AlertTriangle className="h-12 w-12" />}
        title={stale ? 'GameVault was updated' : 'This page hit an error'}
        description={stale
          ? 'Reload to get the new version.'
          : 'Reloading usually fixes it. If it keeps happening, check the server logs.'}
        action={
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="px-4 py-2 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors"
          >
            Reload
          </button>
        }
      />
    );
  }
}
