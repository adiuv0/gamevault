import { lazy } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AppShell } from '@/components/layout/AppShell';
import { AuthGate } from '@/components/layout/AuthGate';
import { GalleryShell } from '@/components/layout/GalleryShell';
import { LibraryPage } from '@/pages/LibraryPage';

// Each page is its own chunk: opening the library or a public gallery link no
// longer downloads the import, upload, settings, and duplicate-review screens.
const GamePage = lazy(() => import('@/pages/GamePage').then((m) => ({ default: m.GamePage })));
const TimelinePage = lazy(() => import('@/pages/TimelinePage').then((m) => ({ default: m.TimelinePage })));
const UploadPage = lazy(() => import('@/pages/UploadPage').then((m) => ({ default: m.UploadPage })));
const SteamImportPage = lazy(() => import('@/pages/SteamImportPage').then((m) => ({ default: m.SteamImportPage })));
const SpecialKImportPage = lazy(() => import('@/pages/SpecialKImportPage').then((m) => ({ default: m.SpecialKImportPage })));
const SearchPage = lazy(() => import('@/pages/SearchPage').then((m) => ({ default: m.SearchPage })));
const SettingsPage = lazy(() => import('@/pages/SettingsPage').then((m) => ({ default: m.SettingsPage })));
const DuplicateReviewPage = lazy(() => import('@/pages/DuplicateReviewPage').then((m) => ({ default: m.DuplicateReviewPage })));
const GalleryHomePage = lazy(() => import('@/pages/gallery/GalleryHomePage').then((m) => ({ default: m.GalleryHomePage })));
const GalleryGamePage = lazy(() => import('@/pages/gallery/GalleryGamePage').then((m) => ({ default: m.GalleryGamePage })));

function App() {
  return (
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
          <Route path="/import/specialk" element={<SpecialKImportPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/duplicates" element={<DuplicateReviewPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
