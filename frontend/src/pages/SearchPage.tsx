import { useState, useEffect } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { Search, Filter, Heart, MessageSquare, X, Loader2, Image } from 'lucide-react';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { EmptyState } from '@/components/shared/EmptyState';
import { search } from '@/api/search';
import { listGames } from '@/api/games';
import { formatDate } from '@/lib/formatters';
import type { SearchResult } from '@/api/search';
import type { Game } from '@/lib/types';

export function SearchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get('q') || '';

  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [page, setPage] = useState(1);

  // Filters
  const [showFilters, setShowFilters] = useState(false);
  const [selectedGameId, setSelectedGameId] = useState<number | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [sortBy, setSortBy] = useState<string>('relevance');

  // Game list for filter dropdown
  const [games, setGames] = useState<Game[]>([]);

  useEffect(() => {
    loadGames();
  }, []);

  useEffect(() => {
    if (query) {
      performSearch(1);
    } else {
      setResults([]);
      setTotal(0);
    }
  }, [query, selectedGameId, dateFrom, dateTo, favoritesOnly, sortBy]);

  const loadGames = async () => {
    try {
      const data = await listGames('name');
      setGames(data.games);
    } catch {
      // ignore
    }
  };

  const performSearch = async (pageNum: number) => {
    try {
      setLoading(true);
      const data = await search({
        q: query,
        game_id: selectedGameId || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        favorites_only: favoritesOnly || undefined,
        sort: sortBy,
        page: pageNum,
        limit: 50,
      });
      if (pageNum === 1) {
        setResults(data.results);
      } else {
        setResults((prev) => [...prev, ...data.results]);
      }
      setTotal(data.total);
      setHasMore(data.has_more);
      setPage(pageNum);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const formData = new FormData(e.target as HTMLFormElement);
    const q = (formData.get('q') as string || '').trim();
    if (q) {
      setSearchParams({ q });
    }
  };

  const clearFilters = () => {
    setSelectedGameId(null);
    setDateFrom('');
    setDateTo('');
    setFavoritesOnly(false);
    setSortBy('relevance');
  };

  const hasActiveFilters = selectedGameId || dateFrom || dateTo || favoritesOnly;

  return (
    <div>
      {/* Search header */}
      <div className="mb-6">
        <form onSubmit={handleSearchSubmit} className="mb-4">
          <div className="relative max-w-2xl">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-text-muted" />
            <input
              type="text"
              name="q"
              defaultValue={query}
              placeholder="Search screenshots and annotations..."
              className="w-full pl-11 pr-4 py-2.5 bg-bg-secondary border border-border rounded-lg text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary text-sm"
              autoFocus
            />
          </div>
        </form>

        {/* Filter toggle + sort */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowFilters(!showFilters)}
              className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded-md border transition-colors ${
                showFilters || hasActiveFilters
                  ? 'border-accent-primary text-accent-primary bg-accent-primary/10'
                  : 'border-border text-text-secondary hover:text-text-primary'
              }`}
            >
              <Filter className="h-4 w-4" />
              Filters
              {hasActiveFilters && (
                <span className="bg-accent-primary text-white text-xs rounded-full w-4 h-4 flex items-center justify-center">
                  !
                </span>
              )}
            </button>

            {query && (
              <span className="text-sm text-text-muted">
                {total} {total === 1 ? 'result' : 'results'}
                {query && ` for "${query}"`}
              </span>
            )}
          </div>

          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="px-3 py-1.5 bg-bg-secondary border border-border rounded-md text-sm text-text-primary focus:outline-none focus:border-accent-primary"
          >
            <option value="relevance">Most Relevant</option>
            <option value="date_desc">Newest First</option>
            <option value="date_asc">Oldest First</option>
          </select>
        </div>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="mb-6 p-4 bg-bg-secondary border border-border rounded-lg">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-text-primary">Filters</h3>
            {hasActiveFilters && (
              <button
                onClick={clearFilters}
                className="flex items-center gap-1 text-xs text-text-muted hover:text-text-primary transition-colors"
              >
                <X className="h-3 w-3" />
                Clear all
              </button>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Game filter */}
            <div>
              <label className="block text-xs text-text-muted mb-1">Game</label>
              <select
                value={selectedGameId || ''}
                onChange={(e) => setSelectedGameId(e.target.value ? Number(e.target.value) : null)}
                className="w-full px-2 py-1.5 bg-bg-primary border border-border rounded text-sm text-text-primary focus:outline-none focus:border-accent-primary"
              >
                <option value="">All games</option>
                {games.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Date from */}
            <div>
              <label className="block text-xs text-text-muted mb-1">From</label>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="w-full px-2 py-1.5 bg-bg-primary border border-border rounded text-sm text-text-primary focus:outline-none focus:border-accent-primary"
              />
            </div>

            {/* Date to */}
            <div>
              <label className="block text-xs text-text-muted mb-1">To</label>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="w-full px-2 py-1.5 bg-bg-primary border border-border rounded text-sm text-text-primary focus:outline-none focus:border-accent-primary"
              />
            </div>

            {/* Favorites only */}
            <div className="flex items-end">
              <label className="flex items-center gap-2 cursor-pointer py-1.5">
                <input
                  type="checkbox"
                  checked={favoritesOnly}
                  onChange={(e) => setFavoritesOnly(e.target.checked)}
                  className="rounded border-border text-accent-primary focus:ring-accent-primary"
                />
                <span className="text-sm text-text-secondary">
                  <Heart className="h-3 w-3 inline mr-1 text-red-400" />
                  Favorites only
                </span>
              </label>
            </div>
          </div>
        </div>
      )}

      {/* Results */}
      {!query && !loading && (
        <EmptyState
          icon={<Search className="h-12 w-12" />}
          title="Search your screenshots"
          description="Search across annotations, game names, filenames, and Steam descriptions."
        />
      )}

      {loading && results.length === 0 && (
        <div className="flex items-center justify-center py-12">
          <LoadingSpinner size="lg" />
        </div>
      )}

      {query && !loading && results.length === 0 && (
        <EmptyState
          icon={<Search className="h-12 w-12" />}
          title="No results found"
          description={`No screenshots matching "${query}". Try a different search term or adjust your filters.`}
        />
      )}

      {results.length > 0 && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {results.map((result) => (
              <SearchResultCard key={result.screenshot_id} result={result} />
            ))}
          </div>

          {hasMore && (
            <div className="flex justify-center mt-6">
              <button
                onClick={() => performSearch(page + 1)}
                disabled={loading}
                className="flex items-center gap-2 px-6 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-primary hover:bg-bg-tertiary transition-colors disabled:opacity-50"
              >
                {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                Load More
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SearchResultCard({ result }: { result: SearchResult }) {
  const thumbnailUrl = result.screenshot_id
    ? `/api/screenshots/${result.screenshot_id}/thumb/md`
    : null;

  return (
    <Link
      to={`/games/${result.game_id}`}
      className="group block rounded-lg overflow-hidden bg-bg-secondary border border-border hover:border-accent-primary/50 transition-all duration-200 no-underline"
    >
      {/* Thumbnail */}
      <div className="relative aspect-video bg-bg-tertiary overflow-hidden">
        {thumbnailUrl ? (
          <img
            src={thumbnailUrl}
            alt={result.filename}
            className="w-full h-full object-cover group-hover:brightness-110 transition-all duration-200"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Image className="h-8 w-8 text-text-muted" />
          </div>
        )}

        {/* Badges */}
        <div className="absolute top-2 right-2 flex items-center gap-1">
          {result.is_favorite && (
            <div className="bg-black/70 backdrop-blur-sm rounded-full p-1">
              <Heart className="h-3 w-3 text-red-400 fill-red-400" />
            </div>
          )}
          {result.has_annotation && (
            <div className="bg-black/70 backdrop-blur-sm rounded-full p-1">
              <MessageSquare className="h-3 w-3 text-accent-primary" />
            </div>
          )}
        </div>
      </div>

      {/* Info */}
      <div className="p-3">
        <p className="text-xs text-accent-primary font-medium mb-0.5">{result.game_name}</p>
        <p className="text-xs text-text-muted">
          {formatDate(result.taken_at || result.uploaded_at)}
        </p>
        {result.annotation_preview && (
          <p className="text-xs text-text-secondary mt-1 line-clamp-2">
            {result.annotation_preview}
          </p>
        )}
      </div>
    </Link>
  );
}
