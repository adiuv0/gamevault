import { Suspense, useState } from 'react';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';

export function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        <TopBar onMenuClick={() => setMobileNavOpen(true)} />
        <main className="flex-1 overflow-y-auto p-4 sm:p-6">
          <Suspense fallback={<div className="py-12"><LoadingSpinner /></div>}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
