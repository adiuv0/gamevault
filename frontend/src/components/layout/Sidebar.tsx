import { NavLink } from 'react-router-dom';
import {
  Gamepad2,
  Clock,
  Upload,
  Download,
  Search,
  Settings,
  Sparkles,
  ScanSearch,
  X,
} from 'lucide-react';

const navItems = [
  { path: '/', label: 'Library', icon: Gamepad2 },
  { path: '/timeline', label: 'Timeline', icon: Clock },
  { path: '/upload', label: 'Upload', icon: Upload },
  { path: '/import/steam', label: 'Steam Import', icon: Download },
  { path: '/import/specialk', label: 'Special K Import', icon: Sparkles },
  { path: '/search', label: 'Search', icon: Search },
  { path: '/duplicates', label: 'Duplicates', icon: ScanSearch },
  { path: '/settings', label: 'Settings', icon: Settings },
];

export function Sidebar({ mobileOpen = false, onClose }: { mobileOpen?: boolean; onClose?: () => void }) {
  return (
    <>
      {mobileOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={onClose}
          className="fixed inset-0 z-30 bg-black/60 md:hidden"
        />
      )}
      <aside className={`fixed inset-y-0 left-0 z-40 w-56 bg-bg-secondary border-r border-border flex flex-col h-screen transition-transform md:sticky md:top-0 md:translate-x-0 ${
        mobileOpen ? 'translate-x-0' : '-translate-x-full'
      }`}>
      {/* Logo */}
      <div className="p-4 border-b border-border flex items-center justify-between">
        <NavLink to="/" className="flex items-center gap-2 no-underline">
          <Gamepad2 className="h-6 w-6 text-accent-primary" />
          <span className="text-lg font-bold text-text-primary">GameVault</span>
        </NavLink>
        <button
          type="button"
          aria-label="Close navigation"
          onClick={onClose}
          className="p-1 text-text-muted hover:text-text-primary md:hidden"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4">
        {navItems.map(({ path, label, icon: Icon }) => (
          <NavLink
            key={path}
            to={path}
            end={path === '/'}
            onClick={onClose}
            className={({ isActive }) =>
              `flex items-center gap-3 px-4 py-2.5 mx-2 rounded-md text-sm no-underline transition-colors ${
                isActive
                  ? 'bg-bg-tertiary text-text-primary'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-tertiary/50'
              }`
            }
          >
            <Icon className="h-4 w-4 flex-shrink-0" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="p-4 border-t border-border">
        <p className="text-xs text-text-muted">GameVault v1.0.3</p>
      </div>
      </aside>
    </>
  );
}
