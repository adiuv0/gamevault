import { useState, useCallback } from 'react';
import { Upload, CheckCircle } from 'lucide-react';
import { UploadZone } from '@/components/upload/UploadZone';
import { UploadQueue } from '@/components/upload/UploadQueue';
import { GameSelector } from '@/components/upload/GameSelector';
import { uploadScreenshots, connectUploadProgress } from '@/api/upload';
import type { UploadFileStatus } from '@/components/upload/UploadItem';

interface SSEEvent {
  type: string;
  status?: string;
  filename?: string;
  file_index?: number;
  current?: number;
  total?: number;
  total_files?: number;
  error?: string;
  reason?: string;
  files?: Array<{
    file_index: number;
    status: 'pending' | 'processing' | 'completed' | 'skipped' | 'failed';
    error_message?: string | null;
  }>;
}

export function UploadPage() {
  const [selectedGameId, setSelectedGameId] = useState<number | null>(null);
  const [uploadItems, setUploadItems] = useState<UploadFileStatus[]>([]);
  const [isUploading, setIsUploading] = useState(false);

  const generatePreviews = (files: File[]): UploadFileStatus[] => {
    return files.map((file) => {
      const preview = URL.createObjectURL(file);
      return {
        file,
        status: 'pending' as const,
        progress: 0,
        preview,
      };
    });
  };

  const handleFilesSelected = useCallback(
    (files: File[]) => {
      const items = generatePreviews(files);
      setUploadItems((prev) => [...prev, ...items]);
    },
    [],
  );

  const handleUpload = async () => {
    if (!selectedGameId || uploadItems.length === 0) return;

    const pendingItems = uploadItems.filter((i) => i.status === 'pending');
    if (pendingItems.length === 0) return;

    setIsUploading(true);

    // Mark all pending as uploading
    setUploadItems((prev) =>
      prev.map((item) =>
        item.status === 'pending' ? { ...item, status: 'uploading' as const } : item,
      ),
    );

    try {
      const files = pendingItems.map((i) => i.file);
      const result = await uploadScreenshots(files, selectedGameId, (loaded, total) => {
        const pct = (loaded / total) * 100;
        setUploadItems((prev) =>
          prev.map((item) =>
            item.status === 'uploading' ? { ...item, progress: pct } : item,
          ),
        );
      });

      // Connect to SSE for processing progress
      // Backend emits: start, file_start, file_complete, file_error, file_skipped, complete
      // We match files by file_index which corresponds to position in pendingItems
      const es = connectUploadProgress(result.task_id, (data) => {
        const event = data as SSEEvent;

        const updateItem = (
          items: UploadFileStatus[],
          fileIndex: number,
          status: UploadFileStatus['status'],
          error?: string,
        ) => {
          const batchItem = pendingItems[fileIndex];
          const targetIndex = batchItem
            ? items.findIndex((item) => item.file === batchItem.file)
            : -1;
          if (targetIndex >= 0) {
            items[targetIndex] = {
              ...items[targetIndex],
              status,
              progress: status === 'uploading' ? items[targetIndex].progress : 100,
              error,
            };
          }
        };

        if (event.type === 'snapshot' && event.files) {
          setUploadItems((prev) => {
            const items = [...prev];
            for (const file of event.files!) {
              const status = file.status === 'completed' || file.status === 'skipped'
                ? 'complete'
                : file.status === 'failed'
                  ? 'error'
                  : 'processing';
              updateItem(items, file.file_index, status, file.error_message ?? undefined);
            }
            return items;
          });
          if (['completed', 'failed', 'cancelled'].includes(event.status ?? '')) {
            setIsUploading(false);
          }
        } else if (event.type === 'file_start' && event.file_index !== undefined) {
          setUploadItems((prev) => {
            const items = [...prev];
            updateItem(items, event.file_index!, 'processing');
            return items;
          });
        } else if (event.type === 'file_complete' && event.file_index !== undefined) {
          setUploadItems((prev) => {
            const items = [...prev];
            updateItem(items, event.file_index!, 'complete');
            return items;
          });
        } else if (event.type === 'file_error' && event.file_index !== undefined) {
          setUploadItems((prev) => {
            const items = [...prev];
            updateItem(items, event.file_index!, 'error', event.error);
            return items;
          });
        } else if (event.type === 'file_skipped' && event.file_index !== undefined) {
          setUploadItems((prev) => {
            const items = [...prev];
            updateItem(items, event.file_index!, 'complete');
            return items;
          });
        } else if (event.type === 'complete') {
          // Mark any remaining uploading items as complete
          setUploadItems((prev) =>
            prev.map((item) =>
              item.status === 'uploading' || item.status === 'processing'
                ? { ...item, status: 'complete' as const }
                : item,
            ),
          );
          setIsUploading(false);
          es.close();
        }
      });
    } catch {
      setUploadItems((prev) =>
        prev.map((item) =>
          item.status === 'uploading'
            ? { ...item, status: 'error' as const, error: 'Upload failed' }
            : item,
        ),
      );
      setIsUploading(false);
    }
  };

  const handleClearCompleted = () => {
    setUploadItems((prev) => {
      prev.forEach((item) => {
        if (item.status === 'complete' && item.preview) {
          URL.revokeObjectURL(item.preview);
        }
      });
      return prev.filter((item) => item.status !== 'complete');
    });
  };

  const pendingCount = uploadItems.filter((i) => i.status === 'pending').length;
  const completedCount = uploadItems.filter((i) => i.status === 'complete').length;

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold text-text-primary mb-6">Upload Screenshots</h1>

      {/* Game selector */}
      <div className="mb-6">
        <label className="block text-sm font-medium text-text-secondary mb-2">
          Select Game
        </label>
        <GameSelector
          selectedGameId={selectedGameId}
          onSelect={setSelectedGameId}
        />
      </div>

      {/* Drop zone */}
      <div className="mb-6">
        <UploadZone
          onFilesSelected={handleFilesSelected}
          disabled={isUploading}
        />
      </div>

      {/* Upload queue */}
      {uploadItems.length > 0 && (
        <div className="mb-6">
          <UploadQueue items={uploadItems} />
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {completedCount > 0 && (
            <button
              onClick={handleClearCompleted}
              className="flex items-center gap-2 px-3 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors"
            >
              <CheckCircle className="h-4 w-4" />
              Clear {completedCount} completed
            </button>
          )}
        </div>

        <button
          onClick={handleUpload}
          disabled={!selectedGameId || pendingCount === 0 || isUploading}
          className="flex items-center gap-2 px-6 py-2.5 bg-accent-primary text-white rounded-md font-medium hover:bg-accent-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Upload className="h-4 w-4" />
          {isUploading
            ? 'Uploading...'
            : `Upload ${pendingCount} ${pendingCount === 1 ? 'file' : 'files'}`}
        </button>
      </div>
    </div>
  );
}
