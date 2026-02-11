import { create } from 'zustand';
import type { ViewMode, SortOption } from '@/lib/types';

interface ViewState {
  viewMode: ViewMode;
  sortBy: SortOption;
  setViewMode: (mode: ViewMode) => void;
  setSortBy: (sort: SortOption) => void;
}

export const useViewStore = create<ViewState>((set) => ({
  viewMode: (localStorage.getItem('gamevault_view') as ViewMode) || 'grid',
  sortBy: (localStorage.getItem('gamevault_sort') as SortOption) || 'name',

  setViewMode: (viewMode: ViewMode) => {
    localStorage.setItem('gamevault_view', viewMode);
    set({ viewMode });
  },

  setSortBy: (sortBy: SortOption) => {
    localStorage.setItem('gamevault_sort', sortBy);
    set({ sortBy });
  },
}));
