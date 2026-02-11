import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Download, ChevronDown, ChevronRight, Loader2, CheckCircle2,
  XCircle, AlertTriangle, User, Search, X, SkipForward, ImageIcon,
} from 'lucide-react';
import { ProgressBar } from '@/components/shared/ProgressBar';
import {
  validateSteam,
  listSteamGames,
  startSteamImport,
  connectImportProgress,
  cancelImport,
} from '@/api/steam';
import type {
  SteamGameInfo,
  SteamCredentials,
  SteamValidateResponse,
} from '@/api/steam';

// ── Types ────────────────────────────────────────────────────────────────────

type Step = 'credentials' | 'games' | 'importing' | 'done';

interface GameProgress {
  appId: number;
  name: string;
  completed: number;
  skipped: number;
  failed: number;
  status: 'pending' | 'importing' | 'done' | 'error';
}

interface ImportResult {
  completed: number;
  skipped: number;
  failed: number;
  totalGames: number;
  status: 'completed' | 'failed' | 'cancelled';
  error?: string;
}

// ── Main Component ───────────────────────────────────────────────────────────

export function SteamImportPage() {
  const [step, setStep] = useState<Step>('credentials');

  // Credentials
  const [userId, setUserId] = useState('');
  const [steamLoginSecure, setSteamLoginSecure] = useState('');
  const [sessionIdCookie, setSessionIdCookie] = useState('');
  const [showCookieGuide, setShowCookieGuide] = useState(false);

  // Validation
  const [validating, setValidating] = useState(false);
  const [validationResult, setValidationResult] = useState<SteamValidateResponse | null>(null);

  // Game selection
  const [games, setGames] = useState<SteamGameInfo[]>([]);
  const [selectedGames, setSelectedGames] = useState<Set<number>>(new Set());
  const [loadingGames, setLoadingGames] = useState(false);
  const [gameFilter, setGameFilter] = useState('');

  // Import progress
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [totalScreenshots, setTotalScreenshots] = useState(0);
  const [overallCompleted, setOverallCompleted] = useState(0);
  const [overallSkipped, setOverallSkipped] = useState(0);
  const [overallFailed, setOverallFailed] = useState(0);
  const [gameProgress, setGameProgress] = useState<Map<number, GameProgress>>(new Map());
  const [currentGameName, setCurrentGameName] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);

  // Cleanup SSE on unmount
  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  // ── Step 1: Validate credentials ─────────────────────────────────────────

  const handleValidate = async () => {
    if (!userId.trim()) return;

    setValidating(true);
    setValidationResult(null);

    try {
      const creds: SteamCredentials = {
        user_id: userId.trim(),
        steam_login_secure: steamLoginSecure.trim() || undefined,
        session_id: sessionIdCookie.trim() || undefined,
      };

      const result = await validateSteam(creds);
      setValidationResult(result);

      if (result.valid) {
        // Auto-advance to game discovery
        setLoadingGames(true);
        try {
          const gameList = await listSteamGames(creds);
          setGames(gameList);
          setSelectedGames(new Set(gameList.map(g => g.app_id)));
          setStep('games');
        } catch (e) {
          setValidationResult({
            valid: false,
            profile_name: result.profile_name,
            avatar_url: result.avatar_url,
            is_numeric_id: result.is_numeric_id,
            error: `Profile found but could not load games: ${e}`,
          });
        } finally {
          setLoadingGames(false);
        }
      }
    } catch (e) {
      setValidationResult({
        valid: false,
        profile_name: null,
        avatar_url: null,
        is_numeric_id: false,
        error: `Connection failed: ${e}`,
      });
    } finally {
      setValidating(false);
    }
  };

  // ── Step 2: Start import ─────────────────────────────────────────────────

  const handleStartImport = async () => {
    if (selectedGames.size === 0) return;

    try {
      const result = await startSteamImport({
        user_id: userId.trim(),
        steam_login_secure: steamLoginSecure.trim() || undefined,
        session_id: sessionIdCookie.trim() || undefined,
        game_ids: Array.from(selectedGames),
        is_numeric_id: validationResult?.is_numeric_id || false,
      });

      setSessionId(result.session_id);
      setStep('importing');

      // Initialize game progress
      const progress = new Map<number, GameProgress>();
      for (const game of games) {
        if (selectedGames.has(game.app_id)) {
          progress.set(game.app_id, {
            appId: game.app_id,
            name: game.name,
            completed: 0,
            skipped: 0,
            failed: 0,
            status: 'pending',
          });
        }
      }
      setGameProgress(progress);

      // Connect to SSE progress stream
      const es = connectImportProgress(
        result.session_id,
        handleSSEEvent,
        () => {
          // On error, don't do anything drastic — the stream may reconnect
        },
      );
      eventSourceRef.current = es;
    } catch (e) {
      setStatusMessage(`Failed to start import: ${e}`);
    }
  };

  // ── SSE event handler ────────────────────────────────────────────────────

  const handleSSEEvent = useCallback((event: string, data: unknown) => {
    const d = data as Record<string, unknown>;

    switch (event) {
      case 'status':
        setStatusMessage(d.message as string || '');
        break;

      case 'profile_validated':
        setStatusMessage(`Profile validated: ${d.profile_name || 'Unknown'}`);
        break;

      case 'games_discovered':
        setTotalScreenshots(d.total_screenshots as number || 0);
        setStatusMessage(`Found ${d.total_games} games with ${d.total_screenshots} screenshots`);
        break;

      case 'game_start':
        setCurrentGameName(d.name as string);
        setStatusMessage(`Importing ${d.name}...`);
        setGameProgress(prev => {
          const next = new Map(prev);
          const existing = next.get(d.app_id as number);
          if (existing) {
            next.set(d.app_id as number, { ...existing, status: 'importing' });
          }
          return next;
        });
        break;

      case 'screenshot_complete':
        setOverallCompleted(d.overall_progress as number || 0);
        setGameProgress(prev => {
          const next = new Map(prev);
          const appId = _findAppIdByName(next, d.game_name as string);
          if (appId !== null) {
            const existing = next.get(appId);
            if (existing) {
              next.set(appId, { ...existing, completed: existing.completed + 1 });
            }
          }
          return next;
        });
        break;

      case 'screenshot_skipped':
        setOverallSkipped(prev => prev + 1);
        setGameProgress(prev => {
          const next = new Map(prev);
          const appId = _findAppIdByName(next, d.game_name as string);
          if (appId !== null) {
            const existing = next.get(appId);
            if (existing) {
              next.set(appId, { ...existing, skipped: existing.skipped + 1 });
            }
          }
          return next;
        });
        break;

      case 'screenshot_failed':
        setOverallFailed(prev => prev + 1);
        setGameProgress(prev => {
          const next = new Map(prev);
          const appId = _findAppIdByName(next, d.game_name as string);
          if (appId !== null) {
            const existing = next.get(appId);
            if (existing) {
              next.set(appId, { ...existing, failed: existing.failed + 1 });
            }
          }
          return next;
        });
        break;

      case 'game_complete':
        setOverallCompleted(d.overall_completed as number || 0);
        setOverallSkipped(d.overall_skipped as number || 0);
        setOverallFailed(d.overall_failed as number || 0);
        setGameProgress(prev => {
          const next = new Map(prev);
          const existing = next.get(d.app_id as number);
          if (existing) {
            next.set(d.app_id as number, {
              ...existing,
              completed: d.completed as number || 0,
              skipped: d.skipped as number || 0,
              failed: d.failed as number || 0,
              status: 'done',
            });
          }
          return next;
        });
        break;

      case 'game_error':
        setGameProgress(prev => {
          const next = new Map(prev);
          const existing = next.get(d.app_id as number);
          if (existing) {
            next.set(d.app_id as number, { ...existing, status: 'error' });
          }
          return next;
        });
        break;

      case 'import_complete':
        setImportResult({
          completed: d.completed as number || 0,
          skipped: d.skipped as number || 0,
          failed: d.failed as number || 0,
          totalGames: d.total_games as number || 0,
          status: 'completed',
        });
        setStep('done');
        setCurrentGameName(null);
        break;

      case 'import_error':
        setImportResult({
          completed: overallCompleted,
          skipped: overallSkipped,
          failed: overallFailed,
          totalGames: gameProgress.size,
          status: 'failed',
          error: d.error as string,
        });
        setStep('done');
        setCurrentGameName(null);
        break;

      case 'import_cancelled':
        setImportResult({
          completed: overallCompleted,
          skipped: overallSkipped,
          failed: overallFailed,
          totalGames: gameProgress.size,
          status: 'cancelled',
        });
        setStep('done');
        setCurrentGameName(null);
        break;

      case 'done':
        eventSourceRef.current?.close();
        eventSourceRef.current = null;
        break;
    }
  }, [overallCompleted, overallSkipped, overallFailed, gameProgress.size]);

  // ── Cancel handler ───────────────────────────────────────────────────────

  const handleCancel = async () => {
    if (!sessionId) return;
    setCancelling(true);
    try {
      await cancelImport(sessionId);
    } catch {
      // ignore — server may have already finished
    }
  };

  // ── Reset to start over ──────────────────────────────────────────────────

  const handleReset = () => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    setStep('credentials');
    setValidationResult(null);
    setGames([]);
    setSelectedGames(new Set());
    setSessionId(null);
    setStatusMessage('');
    setTotalScreenshots(0);
    setOverallCompleted(0);
    setOverallSkipped(0);
    setOverallFailed(0);
    setGameProgress(new Map());
    setCurrentGameName(null);
    setImportResult(null);
    setCancelling(false);
  };

  // ── Filtered games ───────────────────────────────────────────────────────

  const filteredGames = games.filter(g =>
    g.name.toLowerCase().includes(gameFilter.toLowerCase())
  );

  const totalSelected = games.filter(g => selectedGames.has(g.app_id))
    .reduce((sum, g) => sum + g.screenshot_count, 0);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-text-primary mb-6">Steam Import</h1>

      {/* Step indicator */}
      <div className="flex items-center gap-2 mb-6 text-xs text-text-muted">
        <StepIndicator label="Credentials" active={step === 'credentials'} done={step !== 'credentials'} />
        <ChevronRight className="h-3 w-3" />
        <StepIndicator label="Select Games" active={step === 'games'} done={step === 'importing' || step === 'done'} />
        <ChevronRight className="h-3 w-3" />
        <StepIndicator label="Import" active={step === 'importing'} done={step === 'done'} />
      </div>

      {/* Step 1: Credentials */}
      {step === 'credentials' && (
        <div className="space-y-4">
          <div className="bg-bg-secondary border border-border rounded-lg p-6 space-y-4">
            <div className="flex items-start gap-3">
              <Download className="h-5 w-5 text-accent-primary flex-shrink-0 mt-0.5" />
              <div>
                <h2 className="text-lg font-medium text-text-primary">Connect to Steam</h2>
                <p className="text-sm text-text-secondary mt-1">
                  Enter your Steam profile URL or ID. Add cookies to access private screenshots.
                </p>
              </div>
            </div>

            {/* User ID input */}
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Steam ID or Custom URL <span className="text-accent-danger">*</span>
              </label>
              <input
                type="text"
                value={userId}
                onChange={e => setUserId(e.target.value)}
                placeholder="e.g. 76561198012345678 or custom_url_name"
                className="w-full px-3 py-2 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary"
              />
              <p className="text-xs text-text-muted mt-1">
                The numeric Steam ID (76561...) or custom URL from steamcommunity.com/id/<strong>your_name</strong>
              </p>
            </div>

            {/* Cookie guide toggle */}
            <div>
              <button
                onClick={() => setShowCookieGuide(!showCookieGuide)}
                className="flex items-center gap-2 text-sm text-accent-primary hover:text-accent-primary/80 transition-colors"
              >
                {showCookieGuide ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                How to get your Steam cookies (for private screenshots)
              </button>

              {showCookieGuide && (
                <div className="mt-3 bg-bg-primary border border-border rounded-md p-4 text-sm text-text-secondary space-y-3">
                  <p className="font-medium text-text-primary">Step-by-step guide:</p>
                  <ol className="list-decimal list-inside space-y-2">
                    <li>
                      Open <a href="https://steamcommunity.com" target="_blank" rel="noopener noreferrer" className="text-accent-primary hover:underline">steamcommunity.com</a> and log in.
                    </li>
                    <li>
                      Open <strong>Developer Tools</strong> (F12 or Ctrl+Shift+I).
                    </li>
                    <li>
                      Go to the <strong>Application</strong> tab (Chrome) or <strong>Storage</strong> tab (Firefox).
                    </li>
                    <li>
                      Under <strong>Cookies</strong>, click on <code className="px-1 py-0.5 bg-bg-tertiary rounded text-xs">https://steamcommunity.com</code>.
                    </li>
                    <li>
                      Find <code className="px-1 py-0.5 bg-bg-tertiary rounded text-xs">steamLoginSecure</code> and copy its <strong>Value</strong>.
                    </li>
                    <li>
                      Find <code className="px-1 py-0.5 bg-bg-tertiary rounded text-xs">sessionid</code> and copy its <strong>Value</strong>.
                    </li>
                  </ol>
                  <div className="flex items-start gap-2 mt-2 p-2 bg-accent-warning/10 border border-accent-warning/30 rounded">
                    <AlertTriangle className="h-4 w-4 text-accent-warning flex-shrink-0 mt-0.5" />
                    <p className="text-xs text-accent-warning">
                      Cookies expire periodically. If import fails mid-way, you may need to re-extract fresh cookies.
                      These cookies are only sent to Steam servers and never stored by GameVault.
                    </p>
                  </div>
                </div>
              )}
            </div>

            {/* Cookie inputs */}
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                steamLoginSecure <span className="text-text-muted">(optional)</span>
              </label>
              <input
                type="password"
                value={steamLoginSecure}
                onChange={e => setSteamLoginSecure(e.target.value)}
                placeholder="Paste steamLoginSecure cookie value"
                className="w-full px-3 py-2 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary font-mono"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                sessionid <span className="text-text-muted">(optional)</span>
              </label>
              <input
                type="password"
                value={sessionIdCookie}
                onChange={e => setSessionIdCookie(e.target.value)}
                placeholder="Paste sessionid cookie value"
                className="w-full px-3 py-2 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary font-mono"
              />
            </div>

            {/* Validation result */}
            {validationResult && (
              <div className={`flex items-start gap-3 p-3 rounded-md border ${
                validationResult.valid
                  ? 'bg-accent-success/10 border-accent-success/30'
                  : 'bg-accent-danger/10 border-accent-danger/30'
              }`}>
                {validationResult.valid ? (
                  <>
                    {validationResult.avatar_url ? (
                      <img
                        src={validationResult.avatar_url}
                        alt=""
                        className="w-10 h-10 rounded-md flex-shrink-0"
                      />
                    ) : (
                      <User className="h-5 w-5 text-accent-success flex-shrink-0 mt-0.5" />
                    )}
                    <div>
                      <p className="text-sm font-medium text-accent-success">
                        Profile found: {validationResult.profile_name || userId}
                      </p>
                      {loadingGames && (
                        <p className="text-xs text-text-muted mt-1 flex items-center gap-1">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          Loading games...
                        </p>
                      )}
                    </div>
                  </>
                ) : (
                  <>
                    <XCircle className="h-5 w-5 text-accent-danger flex-shrink-0 mt-0.5" />
                    <p className="text-sm text-accent-danger">{validationResult.error}</p>
                  </>
                )}
              </div>
            )}

            {/* Validate button */}
            <button
              onClick={handleValidate}
              disabled={!userId.trim() || validating || loadingGames}
              className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-white rounded-md hover:bg-accent-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {(validating || loadingGames) ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              {validating ? 'Validating...' : loadingGames ? 'Loading games...' : 'Validate & Find Games'}
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Game selection */}
      {step === 'games' && (
        <div className="space-y-4">
          {/* Profile header */}
          {validationResult?.valid && (
            <div className="flex items-center gap-3 bg-bg-secondary border border-border rounded-lg p-4">
              {validationResult.avatar_url ? (
                <img src={validationResult.avatar_url} alt="" className="w-10 h-10 rounded-md" />
              ) : (
                <User className="h-10 w-10 text-text-muted" />
              )}
              <div>
                <p className="text-sm font-medium text-text-primary">
                  {validationResult.profile_name || userId}
                </p>
                <p className="text-xs text-text-muted">
                  {games.length} games with {games.reduce((s, g) => s + g.screenshot_count, 0)} total screenshots
                </p>
              </div>
              <button
                onClick={handleReset}
                className="ml-auto text-xs text-text-muted hover:text-text-primary transition-colors"
              >
                Change profile
              </button>
            </div>
          )}

          {/* Game list */}
          <div className="bg-bg-secondary border border-border rounded-lg overflow-hidden">
            {/* Header + filter */}
            <div className="p-4 border-b border-border">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium text-text-primary">
                  Select games to import ({selectedGames.size} selected, ~{totalSelected} screenshots)
                </h3>
                <div className="flex gap-2">
                  <button
                    onClick={() => setSelectedGames(new Set(games.map(g => g.app_id)))}
                    className="text-xs text-accent-primary hover:text-accent-primary/80"
                  >
                    Select all
                  </button>
                  <span className="text-xs text-text-muted">|</span>
                  <button
                    onClick={() => setSelectedGames(new Set())}
                    className="text-xs text-accent-primary hover:text-accent-primary/80"
                  >
                    Select none
                  </button>
                </div>
              </div>

              {games.length > 10 && (
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-text-muted" />
                  <input
                    type="text"
                    value={gameFilter}
                    onChange={e => setGameFilter(e.target.value)}
                    placeholder="Filter games..."
                    className="w-full pl-9 pr-3 py-1.5 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary"
                  />
                </div>
              )}
            </div>

            {/* Game rows */}
            <div className="max-h-96 overflow-y-auto divide-y divide-border">
              {filteredGames.map(game => (
                <label
                  key={game.app_id}
                  className="flex items-center gap-3 px-4 py-2.5 hover:bg-bg-tertiary cursor-pointer transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={selectedGames.has(game.app_id)}
                    onChange={() => {
                      setSelectedGames(prev => {
                        const next = new Set(prev);
                        if (next.has(game.app_id)) {
                          next.delete(game.app_id);
                        } else {
                          next.add(game.app_id);
                        }
                        return next;
                      });
                    }}
                    className="h-4 w-4 rounded border-border bg-bg-primary text-accent-primary focus:ring-accent-primary"
                  />
                  <span className="text-sm text-text-primary flex-1 min-w-0 truncate">
                    {game.name}
                  </span>
                  <span className="text-xs text-text-muted flex-shrink-0">
                    {game.screenshot_count} screenshot{game.screenshot_count !== 1 ? 's' : ''}
                  </span>
                </label>
              ))}

              {filteredGames.length === 0 && (
                <div className="px-4 py-8 text-center text-sm text-text-muted">
                  No games match the filter.
                </div>
              )}
            </div>
          </div>

          {/* Start import */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleStartImport}
              disabled={selectedGames.size === 0}
              className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-white rounded-md hover:bg-accent-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Download className="h-4 w-4" />
              Import {selectedGames.size} Game{selectedGames.size !== 1 ? 's' : ''} ({totalSelected} screenshots)
            </button>
            <button
              onClick={handleReset}
              className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Step 3: Importing */}
      {step === 'importing' && (
        <div className="space-y-4">
          {/* Overall progress */}
          <div className="bg-bg-secondary border border-border rounded-lg p-6 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-medium text-text-primary flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin text-accent-primary" />
                  Importing Screenshots
                </h3>
                <p className="text-xs text-text-muted mt-1">{statusMessage}</p>
              </div>
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="flex items-center gap-1 px-3 py-1.5 text-xs text-accent-danger border border-accent-danger/30 rounded-md hover:bg-accent-danger/10 transition-colors disabled:opacity-50"
              >
                {cancelling ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
                {cancelling ? 'Cancelling...' : 'Cancel'}
              </button>
            </div>

            {totalScreenshots > 0 && (
              <>
                <ProgressBar
                  value={totalScreenshots > 0 ? ((overallCompleted + overallSkipped) / totalScreenshots) * 100 : 0}
                  label={`Overall: ${overallCompleted} imported, ${overallSkipped} skipped, ${overallFailed} failed of ~${totalScreenshots}`}
                />

                {currentGameName && (
                  <p className="text-xs text-text-secondary">
                    Currently importing: <span className="text-text-primary font-medium">{currentGameName}</span>
                  </p>
                )}
              </>
            )}
          </div>

          {/* Per-game progress */}
          <div className="bg-bg-secondary border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <h4 className="text-xs font-medium text-text-muted uppercase tracking-wider">Per-Game Progress</h4>
            </div>
            <div className="divide-y divide-border max-h-64 overflow-y-auto">
              {Array.from(gameProgress.values()).map(gp => (
                <div key={gp.appId} className="px-4 py-2.5 flex items-center gap-3">
                  <div className="flex-shrink-0">
                    {gp.status === 'pending' && <div className="h-4 w-4 rounded-full border-2 border-text-muted" />}
                    {gp.status === 'importing' && <Loader2 className="h-4 w-4 animate-spin text-accent-primary" />}
                    {gp.status === 'done' && <CheckCircle2 className="h-4 w-4 text-accent-success" />}
                    {gp.status === 'error' && <XCircle className="h-4 w-4 text-accent-danger" />}
                  </div>
                  <span className="text-sm text-text-primary flex-1 min-w-0 truncate">{gp.name}</span>
                  {gp.status !== 'pending' && (
                    <div className="flex items-center gap-2 text-xs text-text-muted flex-shrink-0">
                      {gp.completed > 0 && (
                        <span className="flex items-center gap-0.5 text-accent-success">
                          <ImageIcon className="h-3 w-3" />{gp.completed}
                        </span>
                      )}
                      {gp.skipped > 0 && (
                        <span className="flex items-center gap-0.5 text-accent-warning">
                          <SkipForward className="h-3 w-3" />{gp.skipped}
                        </span>
                      )}
                      {gp.failed > 0 && (
                        <span className="flex items-center gap-0.5 text-accent-danger">
                          <XCircle className="h-3 w-3" />{gp.failed}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Step 4: Done */}
      {step === 'done' && importResult && (
        <div className="space-y-4">
          <div className={`bg-bg-secondary border rounded-lg p-6 ${
            importResult.status === 'completed'
              ? 'border-accent-success/30'
              : importResult.status === 'cancelled'
                ? 'border-accent-warning/30'
                : 'border-accent-danger/30'
          }`}>
            <div className="flex items-start gap-3 mb-4">
              {importResult.status === 'completed' ? (
                <CheckCircle2 className="h-6 w-6 text-accent-success flex-shrink-0" />
              ) : importResult.status === 'cancelled' ? (
                <AlertTriangle className="h-6 w-6 text-accent-warning flex-shrink-0" />
              ) : (
                <XCircle className="h-6 w-6 text-accent-danger flex-shrink-0" />
              )}
              <div>
                <h3 className="text-lg font-medium text-text-primary">
                  {importResult.status === 'completed' ? 'Import Complete!' :
                   importResult.status === 'cancelled' ? 'Import Cancelled' :
                   'Import Failed'}
                </h3>
                {importResult.error && (
                  <p className="text-sm text-accent-danger mt-1">{importResult.error}</p>
                )}
              </div>
            </div>

            {/* Summary stats */}
            <div className="grid grid-cols-3 gap-4 mt-4">
              <div className="bg-bg-primary rounded-md p-3 text-center">
                <p className="text-2xl font-bold text-accent-success">{importResult.completed}</p>
                <p className="text-xs text-text-muted">Imported</p>
              </div>
              <div className="bg-bg-primary rounded-md p-3 text-center">
                <p className="text-2xl font-bold text-accent-warning">{importResult.skipped}</p>
                <p className="text-xs text-text-muted">Skipped (duplicates)</p>
              </div>
              <div className="bg-bg-primary rounded-md p-3 text-center">
                <p className="text-2xl font-bold text-accent-danger">{importResult.failed}</p>
                <p className="text-xs text-text-muted">Failed</p>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <a
              href="/"
              className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-white rounded-md hover:bg-accent-primary/90 transition-colors"
            >
              View Library
            </a>
            <button
              onClick={handleReset}
              className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors"
            >
              Import More
            </button>
          </div>
        </div>
      )}
    </div>
  );
}


// ── Helper Components ────────────────────────────────────────────────────────

function StepIndicator({ label, active, done }: { label: string; active: boolean; done: boolean }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs ${
      active ? 'bg-accent-primary/20 text-accent-primary font-medium' :
      done ? 'text-accent-success' :
      'text-text-muted'
    }`}>
      {done && !active ? '✓ ' : ''}{label}
    </span>
  );
}


// ── Helpers ──────────────────────────────────────────────────────────────────

function _findAppIdByName(map: Map<number, GameProgress>, name: string): number | null {
  for (const [appId, gp] of map) {
    if (gp.name === name) return appId;
  }
  return null;
}
