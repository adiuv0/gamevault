import { useEffect, type ReactNode } from 'react';
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

  useEffect(() => {
    checkAuth();
  }, [token]);

  async function checkAuth() {
    setLoading(true);
    try {
      const authStatus = await api.get<AuthStatus>('/auth/verify');
      setStatus(authStatus);

      // If auth is disabled, we're automatically authenticated
      if (authStatus.auth_disabled) {
        setLoading(false);
        return;
      }

      // If setup is required, show setup flow
      if (authStatus.setup_required) {
        setLoading(false);
        return;
      }

      // If we have a token, try to verify it by making an authenticated request
      if (token) {
        try {
          await api.get('/settings');
          setStatus({ ...authStatus, authenticated: true });
        } catch {
          // Token is invalid, clear it
          useAuthStore.getState().clearToken();
          setStatus({ ...authStatus, authenticated: false });
        }
      }
    } catch {
      // API unreachable
      setStatus({ authenticated: false, setup_required: false, auth_disabled: false });
    }
    setLoading(false);
  }

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

  // Need to verify token
  if (token && !status?.authenticated) {
    return <>{children}</>;
  }

  return <>{children}</>;
}
