import { UploadItem } from './UploadItem';
import type { UploadFileStatus } from './UploadItem';

interface UploadQueueProps {
  items: UploadFileStatus[];
}

export function UploadQueue({ items }: UploadQueueProps) {
  if (items.length === 0) return null;

  const completed = items.filter((i) => i.status === 'complete').length;
  const failed = items.filter((i) => i.status === 'error').length;
  const total = items.length;

  return (
    <div>
      {/* Summary bar */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-text-primary">
          Upload Queue
        </h3>
        <div className="flex items-center gap-3 text-xs">
          {completed > 0 && (
            <span className="text-accent-success">{completed} done</span>
          )}
          {failed > 0 && (
            <span className="text-accent-danger">{failed} failed</span>
          )}
          <span className="text-text-muted">{total} total</span>
        </div>
      </div>

      {/* File list */}
      <div className="space-y-2 max-h-96 overflow-y-auto">
        {items.map((item, index) => (
          <UploadItem key={`${item.file.name}-${index}`} item={item} />
        ))}
      </div>
    </div>
  );
}
