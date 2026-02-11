import { create } from 'zustand';
import type { AuthStatus } from '@/lib/types';

interface AuthState {
  token: string | null;
  status: AuthStatus | null;
  isLoading: boolean;

  setToken: (token: string) => void;
  clearToken: () => void;
  setStatus: (status: AuthStatus) => void;
  setLoading: (loading: boolean) => void;

  isAuthenticated: () => boolean;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: localStorage.getItem('gamevault_token'),
  status: null,
  isLoading: true,

  setToken: (token: string) => {
    localStorage.setItem('gamevault_token', token);
    set({ token });
  },

  clearToken: () => {
    localStorage.removeItem('gamevault_token');
    set({ token: null });
  },

  setStatus: (status: AuthStatus) => set({ status }),
  setLoading: (isLoading: boolean) => set({ isLoading }),

  isAuthenticated: () => {
    const { token, status } = get();
    if (status?.auth_disabled) return true;
    return !!token;
  },
}));
