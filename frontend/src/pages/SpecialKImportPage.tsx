import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ChevronRight,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Search,
  Folder,
  Sparkles,
  X,
  ImageIcon,
  SkipForward,
  HardDrive,
} from 'lucide-react';
import { ProgressBar } from '@/components/shared/ProgressBar';
import {
  cancelSpecialKImport,
  connectSpecialKProgress,
  scanSpecialK,
  startSpecialKImport,
} from '@/api/specialk';
import type { SpecialKScanGame, SpecialKScanResponse } from '@/api/specialk';
import { getSettings, savePreferences } from '@/api/settings';

type Step = 'path' | 'games' | 'importing' | 'done';

interface GameProgress {
  folderName: string;
  name: string;
  completed: number;
  skipped: number;
  failed: number;
  status: 'pending' | 'importing' | 'done';
}

interface ImportResult {
  completed: number;
  skipped: number;
  failed: number;
  totalGames: number;
  status: 'completed' | 'failed' | 'cancelled';
  error?: string;
}

export function SpecialKImportPage() {
  const [step, setStep] = useState<Step>('path');

  // Path config
  const [path, setPath] = useState('');
  const [savedPath, setSavedPath] = useState('');
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<SpecialKScanResponse | null>(null);

  // Game selection
  const [selectedFolders, setSelectedFolders] = useState<Set<string>>(new Set());
  const [gameFilter, setGameFilter] = useState('');

  // Import progress
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [totalScreenshots, setTotalScreenshots] = useState(0);
  const [overallCompleted, setOverallCompleted] = useState(0);
  const [overallSkipped, setOverallSkipped] = useState(0);
  const [overallFailed, setOverallFailed] = useState(0);
  const [gameProgress, setGameProgress] = useState<Map<string, GameProgress>>(new Map());
  const [currentGameName, setCurrentGameName] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  // Pre-fill path from saved preference
  useEffect(() => {
    getSettings()
      .then((s) => {
        if (s.specialk_path) {
          setPath(s.specialk_path);
          setSavedPath(s.specialk_path);
        }
      })
      .catch(() => {});
  }, []);

  // ── Step 1: Scan path ─────────────────────────────────────────────────────

  const handleScan = async () => {
    const trimmed = path.trim();
    if (!trimmed) return;

    setScanning(true);
    setScanResult(null);
    try {
      const result = await scanSpecialK(trimmed);
      setScanResult(result);
      if (result.valid) {
        // Persist the path on a successful scan so future runs pre-fill it
        if (trimmed !== savedPath) {
          try {
            await savePreferences({ specialk_path: trimmed });
            setSavedPath(trimmed);
          } catch {
            // Non-fatal
          }
        }
        setSelectedFolders(new Set(result.games.map((g) => g.folder_name)));
        setStep('games');
      }
    } catch (e) {
      setScanResult({
        valid: false,
        path: trimmed,
        total_games: 0,
        total_screenshots: 0,
        games: [],
        error: e instanceof Error ? e.message : 'Scan failed',
      });
    } finally {
      setScanning(false);
    }
  };

  // ── Step 2: Start import ──────────────────────────────────────────────────

  const handleStartImport = async () => {
    if (selectedFolders.size === 0 || !scanResult) return;

    try {
      const result = await startSpecialKImport({
        path: scanResult.path,
        folder_names: Array.from(selectedFolders),
      });
      setSessionId(result.session_id);
      setStep('importing');

      const progress = new Map<string, GameProgress>();
      for (const game of scanResult.games) {
        if (selectedFolders.has(game.folder_name)) {
          progress.set(game.folder_name, {
            folderName: game.folder_name,
            name: game.suggested_name,
            completed: 0,
            skipped: 0,
            failed: 0,
            status: 'pending',
          });
        }
      }
      setGameProgress(progress);

      const es = connectSpecialKProgress(result.session_id, handleSSEEvent);
      eventSourceRef.current = es;
    } catch (e) {
      setStatusMessage(`Failed to start import: ${e}`);
    }
  };

  // ── SSE handler ───────────────────────────────────────────────────────────

  const handleSSEEvent = useCallback((event: string, data: unknown) => {
    const d = data as Record<string, unknown>;

    switch (event) {
      case 'status':
        setStatusMessage((d.message as string) || '');
        break;

      case 'games_discovered':
        setTotalScreenshots((d.total_screenshots as number) || 0);
        setStatusMessage(
          `Found ${d.total_games} games with ${d.total_screenshots} screenshots`,
        );
        break;

      case 'game_start':
        setCurrentGameName(d.name as string);
        setStatusMessage(`Importing ${d.name}...`);
        setGameProgress((prev) => {
          const next = new Map(prev);
          const folder = d.folder_name as string;
          const existing = next.get(folder);
          if (existing) {
            next.set(folder, { ...existing, status: 'importing' });
          }
          return next;
        });
        break;

      case 'screenshot_complete':
        setOverallCompleted((d.overall_progress as number) || 0);
        setGameProgress((prev) => {
          const next = new Map(prev);
          const folder = _findFolderByName(next, d.game_name as string);
          if (folder !== null) {
            const existing = next.get(folder);
            if (existing) {
              next.set(folder, { ...existing, completed: existing.completed + 1 });
            }
          }
          return next;
        });
        break;

      case 'screenshot_skipped':
        setOverallSkipped((prev) => prev + 1);
        setGameProgress((prev) => {
          const next = new Map(prev);
          const folder = _findFolderByName(next, d.game_name as string);
          if (folder !== null) {
            const existing = next.get(folder);
            if (existing) {
              next.set(folder, { ...existing, skipped: existing.skipped + 1 });
            }
          }
          return next;
        });
        break;

      case 'screenshot_failed':
        setOverallFailed((prev) => prev + 1);
        setGameProgress((prev) => {
          const next = new Map(prev);
          const folder = _findFolderByName(next, d.game_name as string);
          if (folder !== null) {
            const existing = next.get(folder);
            if (existing) {
              next.set(folder, { ...existing, failed: existing.failed + 1 });
            }
          }
          return next;
        });
        break;

      case 'game_complete':
        setOverallCompleted((d.overall_completed as number) || 0);
        setOverallSkipped((d.overall_skipped as number) || 0);
        setOverallFailed((d.overall_failed as number) || 0);
        setGameProgress((prev) => {
          const next = new Map(prev);
          const folder = d.folder_name as string;
          const existing = next.get(folder);
          if (existing) {
            next.set(folder, {
              ...existing,
              completed: (d.completed as number) || 0,
              skipped: (d.skipped as number) || 0,
              failed: (d.failed as number) || 0,
              status: 'done',
            });
          }
          return next;
        });
        break;

      case 'import_complete':
        setImportResult({
          completed: (d.completed as number) || 0,
          skipped: (d.skipped as number) || 0,
          failed: (d.failed as number) || 0,
          totalGames: (d.total_games as number) || 0,
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

  const handleCancel = async () => {
    if (!sessionId) return;
    setCancelling(true);
    try {
      await cancelSpecialKImport(sessionId);
    } catch {
      // ignore
    }
  };

  const handleReset = () => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    setStep('path');
    setScanResult(null);
    setSelectedFolders(new Set());
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

  const filteredGames = scanResult?.games.filter((g) =>
    g.suggested_name.toLowerCase().includes(gameFilter.toLowerCase())
    || g.folder_name.toLowerCase().includes(gameFilter.toLowerCase()),
  ) ?? [];

  const totalSelected = scanResult?.games
    .filter((g) => selectedFolders.has(g.folder_name))
    .reduce((sum, g) => sum + g.screenshot_count, 0) ?? 0;

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-text-primary mb-6">Special K Import</h1>

      {/* Step indicator */}
      <div className="flex items-center gap-2 mb-6 text-xs text-text-muted">
        <StepIndicator label="Path" active={step === 'path'} done={step !== 'path'} />
        <ChevronRight className="h-3 w-3" />
        <StepIndicator label="Select Games" active={step === 'games'} done={step === 'importing' || step === 'done'} />
        <ChevronRight className="h-3 w-3" />
        <StepIndicator label="Import" active={step === 'importing'} done={step === 'done'} />
      </div>

      {/* Step 1: Path */}
      {step === 'path' && (
        <div className="space-y-4">
          <div className="bg-bg-secondary border border-border rounded-lg p-6 space-y-4">
            <div className="flex items-start gap-3">
              <Sparkles className="h-5 w-5 text-accent-primary flex-shrink-0 mt-0.5" />
              <div>
                <h2 className="text-lg font-medium text-text-primary">Import from Special K</h2>
                <p className="text-sm text-text-secondary mt-1">
                  Point GameVault at the folder Special K writes screenshots to. Each top-level
                  subfolder is treated as a separate game. JXR (HDR) and PNG files are imported;
                  HDR captures are tone-mapped to SDR for the gallery view, and the original is
                  preserved for download.
                </p>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Screenshots root path <span className="text-accent-danger">*</span>
              </label>
              <div className="flex items-center gap-2">
                <Folder className="h-4 w-4 text-text-muted flex-shrink-0" />
                <input
                  type="text"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder="e.g. C:\Users\You\Documents\My Mods\SpecialK\Profiles"
                  className="flex-1 px-3 py-2 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary font-mono"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !scanning && path.trim()) handleScan();
                  }}
                />
              </div>
              <p className="text-xs text-text-muted mt-1">
                As seen by the GameVault server — if running in Docker, mount this path
                into the container and use the in-container path here.
              </p>
            </div>

            {scanResult && !scanResult.valid && (
              <div className="flex items-start gap-2 p-3 bg-accent-danger/10 border border-accent-danger/30 rounded-md">
                <XCircle className="h-4 w-4 text-accent-danger flex-shrink-0 mt-0.5" />
                <p className="text-sm text-accent-danger">{scanResult.error}</p>
              </div>
            )}

            <button
              onClick={handleScan}
              disabled={!path.trim() || scanning}
              className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-white rounded-md hover:bg-accent-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              {scanning ? 'Scanning...' : 'Scan path'}
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Game selection */}
      {step === 'games' && scanResult && scanResult.valid && (
        <div className="space-y-4">
          <div className="flex items-center gap-3 bg-bg-secondary border border-border rounded-lg p-4">
            <HardDrive className="h-6 w-6 text-text-muted" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-mono text-text-primary truncate">{scanResult.path}</p>
              <p className="text-xs text-text-muted">
                {scanResult.total_games} game{scanResult.total_games !== 1 ? 's' : ''} ·{' '}
                {scanResult.total_screenshots} screenshot
                {scanResult.total_screenshots !== 1 ? 's' : ''}
              </p>
            </div>
            <button
              onClick={() => setStep('path')}
              className="text-xs text-text-muted hover:text-text-primary transition-colors"
            >
              Change path
            </button>
          </div>

          <div className="bg-bg-secondary border border-border rounded-lg overflow-hidden">
            <div className="p-4 border-b border-border">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium text-text-primary">
                  Select games to import ({selectedFolders.size} selected,{' '}
                  ~{totalSelected} screenshots)
                </h3>
                <div className="flex gap-2">
                  <button
                    onClick={() =>
                      setSelectedFolders(new Set(scanResult.games.map((g) => g.folder_name)))
                    }
                    className="text-xs text-accent-primary hover:text-accent-primary/80"
                  >
                    Select all
                  </button>
                  <span className="text-xs text-text-muted">|</span>
                  <button
                    onClick={() => setSelectedFolders(new Set())}
                    className="text-xs text-accent-primary hover:text-accent-primary/80"
                  >
                    Select none
                  </button>
                </div>
              </div>

              {scanResult.games.length > 10 && (
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-text-muted" />
                  <input
                    type="text"
                    value={gameFilter}
                    onChange={(e) => setGameFilter(e.target.value)}
                    placeholder="Filter games..."
                    className="w-full pl-9 pr-3 py-1.5 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary"
                  />
                </div>
              )}
            </div>

            <div className="max-h-96 overflow-y-auto divide-y divide-border">
              {filteredGames.map((game: SpecialKScanGame) => (
                <label
                  key={game.folder_name}
                  className="flex items-center gap-3 px-4 py-2.5 hover:bg-bg-tertiary cursor-pointer transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={selectedFolders.has(game.folder_name)}
                    onChange={() => {
                      setSelectedFolders((prev) => {
                        const next = new Set(prev);
                        if (next.has(game.folder_name)) {
                          next.delete(game.folder_name);
                        } else {
                          next.add(game.folder_name);
                        }
                        return next;
                      });
                    }}
                    className="h-4 w-4 rounded border-border bg-bg-primary text-accent-primary focus:ring-accent-primary"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-text-primary truncate">
                        {game.suggested_name}
                      </span>
                      {game.has_hdr && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-accent-primary/20 text-accent-primary rounded">
                          HDR
                        </span>
                      )}
                      {game.has_sdr && !game.has_hdr && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-bg-tertiary text-text-muted rounded">
                          SDR
                        </span>
                      )}
                    </div>
                    {game.suggested_name !== game.folder_name && (
                      <p className="text-xs text-text-muted truncate">
                        from <span className="font-mono">{game.folder_name}</span>
                      </p>
                    )}
                  </div>
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

          <div className="flex items-center gap-3">
            <button
              onClick={handleStartImport}
              disabled={selectedFolders.size === 0}
              className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-white rounded-md hover:bg-accent-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Sparkles className="h-4 w-4" />
              Import {selectedFolders.size} Game{selectedFolders.size !== 1 ? 's' : ''} (
              {totalSelected} screenshot{totalSelected !== 1 ? 's' : ''})
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
                  value={
                    totalScreenshots > 0
                      ? ((overallCompleted + overallSkipped) / totalScreenshots) * 100
                      : 0
                  }
                  label={`Overall: ${overallCompleted} imported, ${overallSkipped} skipped, ${overallFailed} failed of ${totalScreenshots}`}
                />

                {currentGameName && (
                  <p className="text-xs text-text-secondary">
                    Currently importing:{' '}
                    <span className="text-text-primary font-medium">{currentGameName}</span>
                  </p>
                )}
              </>
            )}
          </div>

          <div className="bg-bg-secondary border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <h4 className="text-xs font-medium text-text-muted uppercase tracking-wider">
                Per-Game Progress
              </h4>
            </div>
            <div className="divide-y divide-border max-h-64 overflow-y-auto">
              {Array.from(gameProgress.values()).map((gp) => (
                <div key={gp.folderName} className="px-4 py-2.5 flex items-center gap-3">
                  <div className="flex-shrink-0">
                    {gp.status === 'pending' && (
                      <div className="h-4 w-4 rounded-full border-2 border-text-muted" />
                    )}
                    {gp.status === 'importing' && (
                      <Loader2 className="h-4 w-4 animate-spin text-accent-primary" />
                    )}
                    {gp.status === 'done' && (
                      <CheckCircle2 className="h-4 w-4 text-accent-success" />
                    )}
                  </div>
                  <span className="text-sm text-text-primary flex-1 min-w-0 truncate">
                    {gp.name}
                  </span>
                  {gp.status !== 'pending' && (
                    <div className="flex items-center gap-2 text-xs text-text-muted flex-shrink-0">
                      {gp.completed > 0 && (
                        <span className="flex items-center gap-0.5 text-accent-success">
                          <ImageIcon className="h-3 w-3" />
                          {gp.completed}
                        </span>
                      )}
                      {gp.skipped > 0 && (
                        <span className="flex items-center gap-0.5 text-accent-warning">
                          <SkipForward className="h-3 w-3" />
                          {gp.skipped}
                        </span>
                      )}
                      {gp.failed > 0 && (
                        <span className="flex items-center gap-0.5 text-accent-danger">
                          <XCircle className="h-3 w-3" />
                          {gp.failed}
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
          <div
            className={`bg-bg-secondary border rounded-lg p-6 ${
              importResult.status === 'completed'
                ? 'border-accent-success/30'
                : importResult.status === 'cancelled'
                  ? 'border-accent-warning/30'
                  : 'border-accent-danger/30'
            }`}
          >
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
                  {importResult.status === 'completed'
                    ? 'Import Complete!'
                    : importResult.status === 'cancelled'
                      ? 'Import Cancelled'
                      : 'Import Failed'}
                </h3>
                {importResult.error && (
                  <p className="text-sm text-accent-danger mt-1">{importResult.error}</p>
                )}
              </div>
            </div>

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

function StepIndicator({
  label,
  active,
  done,
}: {
  label: string;
  active: boolean;
  done: boolean;
}) {
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs ${
        active
          ? 'bg-accent-primary/20 text-accent-primary font-medium'
          : done
            ? 'text-accent-success'
            : 'text-text-muted'
      }`}
    >
      {done && !active ? '✓ ' : ''}
      {label}
    </span>
  );
}

function _findFolderByName(map: Map<string, GameProgress>, name: string): string | null {
  for (const [folder, gp] of map) {
    if (gp.name === name) return folder;
  }
  return null;
}
