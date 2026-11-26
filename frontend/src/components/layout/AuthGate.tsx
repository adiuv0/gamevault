import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { api } from '@/api/client';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { LoginPage } from '@/pages/LoginPage';
import type { AuthStatus } from '@/lib/types';

interface AuthGateProps {
  children: ReactNode;
}

export function AuthGate({ children }: AuthGateProps) {
  const { status, isLoading, setStatus, setLoading, token } = useAuthStore();
  const [unreachable, setUnreachable] = useState(false);

  // Runs on mount (and on Retry) only. Depending on the token re-ran it after
  // every login, logout and password change, blanking the app each time.
  // Resolves to false when the server couldn't be reached.
  const checkAuth = useCallback(async (): Promise<boolean> => {
    setLoading(true);
    try {
      const authStatus = await api.get<AuthStatus>('/auth/verify');
      setStatus(authStatus);

      // If auth is disabled, we're automatically authenticated
      if (authStatus.auth_disabled) {
        setLoading(false);
        return true;
      }

      // If setup is required, show setup flow
      if (authStatus.setup_required) {
        setLoading(false);
        return true;
      }

      // /auth/verify validates the stored token itself, so no second request is needed.
      if (useAuthStore.getState().token && !authStatus.authenticated) {
        useAuthStore.getState().clearToken();
      }
    } catch {
      // API unreachable: say so instead of opening an app that can't load anything.
      // Loading ends in finishCheck, together with the unreachable flag.
      return false;
    }
    setLoading(false);
    return true;
  }, [setLoading, setStatus]);

  const finishCheck = useCallback((reachable: boolean) => {
    setUnreachable(!reachable);
    if (!reachable) setLoading(false);
  }, [setLoading]);

  useEffect(() => {
    void checkAuth().then(finishCheck);
  }, [checkAuth, finishCheck]);

  if (isLoading) {
    return (
      <div className="min-h-screen bg-bg-primary flex items-center justify-center">
        <div className="text-center">
          <LoadingSpinner size="lg" />
          <p className="mt-4 text-text-secondary">Loading GameVault...</p>
        </div>
      </div>
    );
  }

  if (unreachable) {
    return (
      <div className="min-h-screen bg-bg-primary flex items-center justify-center p-4">
        <div className="text-center max-w-sm">
          <p className="text-text-primary font-medium">Can't reach the GameVault server</p>
          <p className="mt-2 text-sm text-text-secondary">
            It may be restarting after an update. Try again in a moment.
          </p>
          <button
            type="button"
            onClick={() => {
              setUnreachable(false);
              void checkAuth().then(finishCheck);
            }}
            className="mt-4 px-4 py-2 bg-accent-primary text-on-accent rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  // Auth disabled — skip login entirely
  if (status?.auth_disabled) {
    return <>{children}</>;
  }

  // Setup required — show setup flow
  if (status?.setup_required) {
    return <LoginPage mode="setup" />;
  }

  // Not authenticated — show login
  if (!status?.authenticated && !token) {
    return <LoginPage mode="login" />;
  }

  return <>{children}</>;
}
