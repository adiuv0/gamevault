import { useState } from 'react';
import { Gamepad2, Eye, EyeOff } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { api } from '@/api/client';
import type { AuthResponse } from '@/lib/types';

interface LoginPageProps {
  mode?: 'login' | 'setup';
}

export function LoginPage({ mode = 'login' }: LoginPageProps) {
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const { setToken, setStatus } = useAuthStore();

  const isSetup = mode === 'setup';

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');

    if (isSetup) {
      if (password.length < 6) {
        setError('Password must be at least 6 characters');
        return;
      }
      if (password !== confirmPassword) {
        setError('Passwords do not match');
        return;
      }
    }

    setLoading(true);
    try {
      const endpoint = isSetup ? '/auth/setup' : '/auth/login';
      const result = await api.post<AuthResponse>(endpoint, { password });
      setToken(result.token);
      setStatus({
        authenticated: true,
        setup_required: false,
        auth_disabled: false,
      });
      // Force reload to re-render with authenticated state
      window.location.reload();
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'detail' in err) {
        setError(String((err as { detail: string }).detail));
      } else {
        setError(isSetup ? 'Failed to set up password' : 'Invalid password');
      }
    }
    setLoading(false);
  }

  return (
    <div className="min-h-screen bg-bg-primary flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-bg-secondary border border-border mb-4">
            <Gamepad2 className="h-8 w-8 text-accent-primary" />
          </div>
          <h1 className="text-2xl font-bold text-text-primary">GameVault</h1>
          <p className="text-sm text-text-secondary mt-1">
            {isSetup
              ? 'Set up your password to get started'
              : 'Enter your password to continue'}
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-text-secondary mb-1.5"
            >
              Password
            </label>
            <div className="relative">
              <input
                id="password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isSetup ? 'Choose a password (min 6 chars)' : 'Enter your password'}
                className="w-full px-3 py-2 pr-10 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-primary transition-colors"
                autoFocus
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-text-muted hover:text-text-secondary"
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          {isSetup && (
            <div>
              <label
                htmlFor="confirmPassword"
                className="block text-sm font-medium text-text-secondary mb-1.5"
              >
                Confirm Password
              </label>
              <input
                id="confirmPassword"
                type={showPassword ? 'text' : 'password'}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Confirm your password"
                className="w-full px-3 py-2 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-primary transition-colors"
                required
              />
            </div>
          )}

          {error && (
            <p className="text-sm text-accent-danger bg-accent-danger/10 border border-accent-danger/20 rounded-md px-3 py-2">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 px-4 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading
              ? 'Please wait...'
              : isSetup
                ? 'Set Password & Continue'
                : 'Login'}
          </button>
        </form>
      </div>
    </div>
  );
}
