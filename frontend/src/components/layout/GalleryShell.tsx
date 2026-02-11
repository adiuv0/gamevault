import { Outlet, Link } from 'react-router-dom';
import { Gamepad2 } from 'lucide-react';

export function GalleryShell() {
  return (
    <div className="min-h-screen flex flex-col bg-bg-primary">
      {/* Header */}
      <header className="border-b border-border bg-bg-secondary/80 backdrop-blur-sm sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center h-14">
            <Link
              to="/gallery"
              className="flex items-center gap-2 text-text-primary no-underline hover:text-accent-primary transition-colors"
            >
              <Gamepad2 className="h-5 w-5" />
              <span className="font-semibold text-lg">GameVault Gallery</span>
            </Link>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 w-full">
        <Outlet />
      </main>

      {/* Footer */}
      <footer className="border-t border-border py-4 text-center text-xs text-text-muted">
        Powered by GameVault
      </footer>
    </div>
  );
}
