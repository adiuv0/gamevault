import { useCallback, useEffect, useState } from 'react';
import { Check, GitMerge, Loader2, ScanSearch, X } from 'lucide-react';
import {
  dismissDuplicateCandidate,
  getDuplicateScan,
  groupDuplicateCandidate,
  listDuplicateCandidates,
  startDuplicateScan,
} from '@/api/duplicates';
import type {
  DuplicateCandidate,
  DuplicateScanJob,
  DuplicateStatus,
} from '@/api/duplicates';
import { getScreenshotImageUrl, getThumbnailUrl } from '@/api/screenshots';
import { EmptyState } from '@/components/shared/EmptyState';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { formatDate } from '@/lib/formatters';

const statuses: Array<{ value: DuplicateStatus; label: string }> = [
  { value: 'pending', label: 'Pending' },
  { value: 'grouped', label: 'Grouped' },
  { value: 'dismissed', label: 'Dismissed' },
];

export function DuplicateReviewPage() {
  const [job, setJob] = useState<DuplicateScanJob | null>(null);
  const [status, setStatus] = useState<DuplicateStatus>('pending');
  const [candidates, setCandidates] = useState<DuplicateCandidate[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadCandidates = useCallback(async (nextStatus = status, nextPage = 1) => {
    const data = await listDuplicateCandidates(nextStatus, nextPage);
    setCandidates((current) => nextPage === 1 ? data.candidates : [...current, ...data.candidates]);
    setTotal(data.total);
    setPage(nextPage);
    setHasMore(data.has_more);
  }, [status]);

  const loadInitial = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [scan, candidatePage] = await Promise.all([
        getDuplicateScan(),
        listDuplicateCandidates(status),
      ]);
      setJob(scan);
      setCandidates(candidatePage.candidates);
      setTotal(candidatePage.total);
      setHasMore(candidatePage.has_more);
      setPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load duplicate review');
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  useEffect(() => {
    if (!job || !['pending', 'running'].includes(job.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const latest = await getDuplicateScan();
        setJob(latest);
        if (latest?.status === 'completed' || latest?.status === 'failed') {
          setScanning(false);
          await loadCandidates(status, 1);
        }
      } catch {
        // Keep the last durable status visible and retry on the next tick.
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [job, loadCandidates, status]);

  const handleScan = async () => {
    try {
      setScanning(true);
      setError(null);
      setJob(await startDuplicateScan());
    } catch (err) {
      setScanning(false);
      setError(err instanceof Error ? err.message : 'Scan failed to start');
    }
  };

  const handleStatusChange = (nextStatus: DuplicateStatus) => {
    setStatus(nextStatus);
  };

  const handleDecision = async (candidate: DuplicateCandidate, group: boolean) => {
    try {
      setActingId(candidate.id);
      if (group) {
        await groupDuplicateCandidate(candidate.id);
      } else {
        await dismissDuplicateCandidate(candidate.id);
      }
      await loadCandidates(status, 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save decision');
    } finally {
      setActingId(null);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-24"><LoadingSpinner size="lg" /></div>;
  }

  const progress = job?.total ? Math.min(100, (job.processed / job.total) * 100) : 0;
  const scanActive = scanning || job?.status === 'pending' || job?.status === 'running';

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <ScanSearch className="h-6 w-6 text-accent-primary" />
          <div>
            <h1 className="text-2xl font-bold text-text-primary">Duplicate Review</h1>
            <p className="text-sm text-text-muted">{total.toLocaleString()} {status}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={handleScan}
          disabled={scanActive}
          className="flex items-center gap-2 px-3 py-2 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 disabled:opacity-50"
        >
          {scanActive ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanSearch className="h-4 w-4" />}
          Scan library
        </button>
      </div>

      {job && (
        <div className="mb-5 border-y border-border py-3">
          <div className="flex items-center justify-between text-xs text-text-muted mb-2">
            <span className="capitalize">{job.status}</span>
            <span>{job.processed.toLocaleString()} / {job.total.toLocaleString()}</span>
          </div>
          <div className="h-1.5 bg-bg-tertiary overflow-hidden">
            <div className="h-full bg-accent-primary transition-all" style={{ width: `${progress}%` }} />
          </div>
          {job.errors > 0 && <p className="text-xs text-accent-warning mt-2">{job.errors} files could not be scanned</p>}
          {job.error_message && <p className="text-xs text-accent-danger mt-2">{job.error_message}</p>}
        </div>
      )}

      {error && <p className="mb-4 text-sm text-accent-danger">{error}</p>}

      <div className="inline-flex bg-bg-secondary border border-border rounded-md p-1 mb-5">
        {statuses.map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => handleStatusChange(item.value)}
            className={`px-3 py-1.5 rounded text-sm transition-colors ${
              status === item.value
                ? 'bg-bg-tertiary text-text-primary'
                : 'text-text-muted hover:text-text-primary'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {candidates.length === 0 ? (
        <EmptyState
          icon={status === 'pending'
            ? <ScanSearch className="h-8 w-8" />
            : <Check className="h-8 w-8" />}
          title={status === 'pending' ? 'No matches awaiting review' : `No ${status} matches`}
          description=""
        />
      ) : (
        <div className="space-y-4">
          {candidates.map((candidate) => (
            <CandidateRow
              key={candidate.id}
              candidate={candidate}
              acting={actingId === candidate.id}
              onDecision={handleDecision}
            />
          ))}
        </div>
      )}

      {hasMore && (
        <div className="flex justify-center mt-5">
          <button
            type="button"
            onClick={async () => {
              setLoadingMore(true);
              try { await loadCandidates(status, page + 1); } finally { setLoadingMore(false); }
            }}
            disabled={loadingMore}
            className="flex items-center gap-2 px-3 py-2 bg-bg-secondary border border-border rounded-md text-sm text-text-primary disabled:opacity-50"
          >
            {loadingMore && <Loader2 className="h-4 w-4 animate-spin" />}
            Load more
          </button>
        </div>
      )}
    </div>
  );
}

function CandidateRow({
  candidate,
  acting,
  onDecision,
}: {
  candidate: DuplicateCandidate;
  acting: boolean;
  onDecision: (candidate: DuplicateCandidate, group: boolean) => Promise<void>;
}) {
  return (
    <article className="bg-bg-secondary border border-border rounded-lg overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-border">
        <span className="text-sm font-medium text-text-primary">{candidate.game_name}</span>
        <span className={`text-xs px-2 py-1 rounded ${
          candidate.confidence === 'likely'
            ? 'bg-accent-warning/10 text-accent-warning'
            : 'bg-bg-tertiary text-text-muted'
        }`}>
          {candidate.confidence} · {candidate.hash_distance} bits
        </span>
      </div>

      <div className="grid md:grid-cols-2 gap-px bg-border">
        <CandidateImage
          id={candidate.left_screenshot_id}
          filename={candidate.left_filename}
          format={candidate.left_format}
          width={candidate.left_width}
          height={candidate.left_height}
          takenAt={candidate.left_taken_at}
        />
        <CandidateImage
          id={candidate.right_screenshot_id}
          filename={candidate.right_filename}
          format={candidate.right_format}
          width={candidate.right_width}
          height={candidate.right_height}
          takenAt={candidate.right_taken_at}
        />
      </div>

      {candidate.status === 'pending' && (
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-border">
          <button
            type="button"
            onClick={() => onDecision(candidate, false)}
            disabled={acting}
            className="flex items-center gap-2 px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary rounded-md disabled:opacity-50"
          >
            <X className="h-4 w-4" />
            Not a match
          </button>
          <button
            type="button"
            onClick={() => onDecision(candidate, true)}
            disabled={acting}
            className="flex items-center gap-2 px-3 py-1.5 text-sm bg-accent-primary text-white rounded-md disabled:opacity-50"
          >
            {acting ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitMerge className="h-4 w-4" />}
            Group variants
          </button>
        </div>
      )}
    </article>
  );
}

function CandidateImage({
  id,
  filename,
  format,
  width,
  height,
  takenAt,
}: {
  id: number;
  filename: string;
  format: string | null;
  width: number | null;
  height: number | null;
  takenAt: string | null;
}) {
  return (
    <div className="bg-bg-secondary min-w-0">
      <a href={getScreenshotImageUrl(id)} target="_blank" rel="noreferrer" className="block bg-black">
        <img
          src={getThumbnailUrl(id, 'md')}
          alt={filename}
          className="w-full aspect-video object-contain"
          loading="lazy"
          decoding="async"
        />
      </a>
      <div className="px-3 py-2 min-w-0">
        <p className="text-sm text-text-primary truncate" title={filename}>{filename}</p>
        <p className="text-xs text-text-muted mt-1">
          {[format?.toUpperCase(), width && height ? `${width} × ${height}` : null, takenAt ? formatDate(takenAt) : null]
            .filter(Boolean)
            .join(' · ')}
        </p>
      </div>
    </div>
  );
}
