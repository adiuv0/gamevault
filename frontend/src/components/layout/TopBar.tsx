import { useNavigate } from 'react-router-dom';
import { Search, Grid3X3, List, LogOut } from 'lucide-react';
import { useState } from 'react';
import { useViewStore } from '@/stores/viewStore';
import { useAuthStore } from '@/stores/authStore';

export function TopBar() {
  const [searchQuery, setSearchQuery] = useState('');
  const navigate = useNavigate();
  const { viewMode, setViewMode } = useViewStore();
  const clearToken = useAuthStore((s) => s.clearToken);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      navigate(`/search?q=${encodeURIComponent(searchQuery.trim())}`);
    }
  };

  const handleLogout = () => {
    clearToken();
    window.location.reload();
  };

  return (
    <header className="h-14 bg-bg-secondary border-b border-border flex items-center px-4 gap-4 sticky top-0 z-10">
      {/* Search */}
      <form onSubmit={handleSearch} className="flex-1 max-w-xl">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-muted" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search screenshots and annotations..."
            className="w-full pl-9 pr-4 py-1.5 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-primary transition-colors"
          />
        </div>
      </form>

      {/* View toggle */}
      <div className="flex items-center border border-border rounded-md overflow-hidden">
        <button
          onClick={() => setViewMode('grid')}
          className={`p-1.5 transition-colors ${
            viewMode === 'grid'
              ? 'bg-bg-tertiary text-text-primary'
              : 'text-text-muted hover:text-text-secondary'
          }`}
          title="Grid view"
        >
          <Grid3X3 className="h-4 w-4" />
        </button>
        <button
          onClick={() => setViewMode('list')}
          className={`p-1.5 transition-colors ${
            viewMode === 'list'
              ? 'bg-bg-tertiary text-text-primary'
              : 'text-text-muted hover:text-text-secondary'
          }`}
          title="List view"
        >
          <List className="h-4 w-4" />
        </button>
      </div>

      {/* Logout */}
      <button
        onClick={handleLogout}
        className="p-1.5 text-text-muted hover:text-text-secondary transition-colors"
        title="Logout"
      >
        <LogOut className="h-4 w-4" />
      </button>
    </header>
  );
}
