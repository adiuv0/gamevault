import { create } from 'zustand';

interface ImportState {
  isImporting: boolean;
  sessionId: number | null;
  totalScreenshots: number;
  completedScreenshots: number;
  currentGame: string | null;

  startImport: (sessionId: number) => void;
  updateProgress: (completed: number, total: number, currentGame: string | null) => void;
  finishImport: () => void;
}

export const useImportStore = create<ImportState>((set) => ({
  isImporting: false,
  sessionId: null,
  totalScreenshots: 0,
  completedScreenshots: 0,
  currentGame: null,

  startImport: (sessionId: number) =>
    set({ isImporting: true, sessionId, completedScreenshots: 0, totalScreenshots: 0 }),

  updateProgress: (completedScreenshots, totalScreenshots, currentGame) =>
    set({ completedScreenshots, totalScreenshots, currentGame }),

  finishImport: () =>
    set({ isImporting: false, sessionId: null, currentGame: null }),
}));
