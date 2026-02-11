import { NavLink } from 'react-router-dom';
import {
  Gamepad2,
  Clock,
  Upload,
  Download,
  Search,
  Settings,
} from 'lucide-react';

const navItems = [
  { path: '/', label: 'Library', icon: Gamepad2 },
  { path: '/timeline', label: 'Timeline', icon: Clock },
  { path: '/upload', label: 'Upload', icon: Upload },
  { path: '/import/steam', label: 'Steam Import', icon: Download },
  { path: '/search', label: 'Search', icon: Search },
  { path: '/settings', label: 'Settings', icon: Settings },
];

export function Sidebar() {
  return (
    <aside className="w-56 bg-bg-secondary border-r border-border flex flex-col h-screen sticky top-0">
      {/* Logo */}
      <div className="p-4 border-b border-border">
        <NavLink to="/" className="flex items-center gap-2 no-underline">
          <Gamepad2 className="h-6 w-6 text-accent-primary" />
          <span className="text-lg font-bold text-text-primary">GameVault</span>
        </NavLink>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4">
        {navItems.map(({ path, label, icon: Icon }) => (
          <NavLink
            key={path}
            to={path}
            end={path === '/'}
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
        <p className="text-xs text-text-muted">GameVault v0.1.0</p>
      </div>
    </aside>
  );
}
