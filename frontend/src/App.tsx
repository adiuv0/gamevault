import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from '@/components/layout/AppShell';
import { AuthGate } from '@/components/layout/AuthGate';
import { GalleryShell } from '@/components/layout/GalleryShell';
import { LibraryPage } from '@/pages/LibraryPage';
import { GamePage } from '@/pages/GamePage';
import { TimelinePage } from '@/pages/TimelinePage';
import { UploadPage } from '@/pages/UploadPage';
import { SteamImportPage } from '@/pages/SteamImportPage';
import { SearchPage } from '@/pages/SearchPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { GalleryHomePage } from '@/pages/gallery/GalleryHomePage';
import { GalleryGamePage } from '@/pages/gallery/GalleryGamePage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      retry: 1,
    },
  },
});

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Public gallery — no auth required */}
          <Route path="/gallery" element={<GalleryShell />}>
            <Route index element={<GalleryHomePage />} />
            <Route path="games/:id" element={<GalleryGamePage />} />
          </Route>

          {/* Admin UI — requires auth */}
          <Route
            element={
              <AuthGate>
                <AppShell />
              </AuthGate>
            }
          >
            <Route path="/" element={<LibraryPage />} />
            <Route path="/games/:id" element={<GamePage />} />
            <Route path="/timeline" element={<TimelinePage />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/import/steam" element={<SteamImportPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
