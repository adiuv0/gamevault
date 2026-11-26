import { useEffect, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
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
  type LucideIcon,
} from 'lucide-react';
import { getCoverUrl, listGames } from '@/api/games';
import type { Game } from '@/lib/types';

interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
}

// What you open the app for.
const browseItems: NavItem[] = [
  { path: '/', label: 'Library', icon: Gamepad2 },
  { path: '/timeline', label: 'Timeline', icon: Clock },
  { path: '/search', label: 'Search', icon: Search },
];

// What you use now and then.
const toolItems: NavItem[] = [
  { path: '/upload', label: 'Upload', icon: Upload },
  { path: '/import/steam', label: 'Steam Import', icon: Download },
  { path: '/import/specialk', label: 'Special K Import', icon: Sparkles },
  { path: '/duplicates', label: 'Duplicates', icon: ScanSearch },
  { path: '/settings', label: 'Settings', icon: Settings },
];

const RECENT_GAMES = 5;
const RECENT_REFRESH_MS = 30_000;

/** The games captured most recently, refreshed at most every 30 s as you move around. */
function useRecentGames(): Game[] {
  const { pathname } = useLocation();
  const [games, setGames] = useState<Game[]>([]);
  const lastFetch = useRef(0);

  useEffect(() => {
    if (Date.now() - lastFetch.current < RECENT_REFRESH_MS) return;
    lastFetch.current = Date.now();
    // Not cancelled on navigation: the answer is the same whichever page asked.
    listGames('date')
      .then((data) => {
        setGames(data.games.filter((g) => g.screenshot_count > 0).slice(0, RECENT_GAMES));
      })
      .catch(() => {
        // The list is a shortcut; the Library page reports a server that is down.
        lastFetch.current = 0;
      });
  }, [pathname]);

  return games;
}

function navClass({ isActive }: { isActive: boolean }) {
  return `flex items-center gap-3 px-3 py-2 mx-2 rounded-md text-[15px] font-medium no-underline transition-colors ${
    isActive
      ? 'bg-bg-tertiary text-text-primary'
      : 'text-text-secondary hover:text-text-primary hover:bg-bg-tertiary/50'
  }`;
}

export function Sidebar({ mobileOpen = false, onClose }: { mobileOpen?: boolean; onClose?: () => void }) {
  const recentGames = useRecentGames();

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
      <aside className={`fixed inset-y-0 left-0 z-40 w-56 bg-[#07080b] border-r border-border flex flex-col h-screen transition-transform md:sticky md:top-0 md:translate-x-0 ${
        mobileOpen ? 'translate-x-0' : '-translate-x-full'
      }`}>
      {/* Logo */}
      <div className="h-14 px-5 flex items-center justify-between flex-shrink-0">
        <NavLink to="/" className="display-title text-[22px] tracking-wide text-text-primary no-underline">
          Game<span className="text-accent-primary">Vault</span>
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

      <nav className="flex-1 overflow-y-auto pt-2 pb-4">
        {browseItems.map(({ path, label, icon: Icon }) => (
          <NavLink key={path} to={path} end={path === '/'} onClick={onClose} className={navClass}>
            <Icon className="h-4 w-4 flex-shrink-0" />
            <span>{label}</span>
          </NavLink>
        ))}

        {recentGames.length > 0 && (
          <>
            <p className="shelf-label !text-text-muted px-5 mt-6 mb-2">Recent</p>
            {recentGames.map((game) => (
              <NavLink
                key={game.id}
                to={`/games/${game.id}`}
                onClick={onClose}
                className={({ isActive }) =>
                  `flex items-center gap-2.5 px-3 py-1.5 mx-2 rounded-md text-sm no-underline transition-colors ${
                    isActive
                      ? 'bg-bg-tertiary text-text-primary'
                      : 'text-text-secondary hover:text-text-primary hover:bg-bg-tertiary/50'
                  }`
                }
              >
                {game.cover_image_path ? (
                  <img
                    src={getCoverUrl(game.id, game.updated_at)}
                    alt=""
                    width={22}
                    height={33}
                    loading="lazy"
                    className="h-[33px] w-[22px] flex-shrink-0 rounded-sm object-cover"
                  />
                ) : (
                  <span className="h-[33px] w-[22px] flex-shrink-0 rounded-sm bg-bg-tertiary" />
                )}
                <span className="min-w-0 flex-1 truncate">{game.name}</span>
                <span className="text-xs text-text-muted tabular-nums">{game.screenshot_count}</span>
              </NavLink>
            ))}
          </>
        )}

        <p className="shelf-label !text-text-muted px-5 mt-6 mb-2">Tools</p>
        {toolItems.map(({ path, label, icon: Icon }) => (
          <NavLink key={path} to={path} onClick={onClose} className={navClass}>
            <Icon className="h-4 w-4 flex-shrink-0" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-3 border-t border-border flex-shrink-0">
        <p className="text-xs text-text-muted">GameVault v{__APP_VERSION__}</p>
      </div>
      </aside>
    </>
  );
}
