import { CheckCircle, XCircle, Loader2, Image } from 'lucide-react';
import { ProgressBar } from '@/components/shared/ProgressBar';

export interface UploadFileStatus {
  file: File;
  status: 'pending' | 'uploading' | 'processing' | 'complete' | 'error';
  progress: number;
  error?: string;
  preview?: string;
}

interface UploadItemProps {
  item: UploadFileStatus;
}

export function UploadItem({ item }: UploadItemProps) {
  const statusIcon = () => {
    switch (item.status) {
      case 'complete':
        return <CheckCircle className="h-5 w-5 text-accent-success flex-shrink-0" />;
      case 'error':
        return <XCircle className="h-5 w-5 text-accent-danger flex-shrink-0" />;
      case 'uploading':
      case 'processing':
        return <Loader2 className="h-5 w-5 text-accent-primary flex-shrink-0 animate-spin" />;
      default:
        return <Image className="h-5 w-5 text-text-muted flex-shrink-0" />;
    }
  };

  const statusText = () => {
    switch (item.status) {
      case 'uploading':
        return `Uploading... ${Math.round(item.progress)}%`;
      case 'processing':
        return 'Processing...';
      case 'complete':
        return 'Done';
      case 'error':
        return item.error || 'Failed';
      default:
        return 'Waiting...';
    }
  };

  const fileSizeMB = (item.file.size / (1024 * 1024)).toFixed(1);

  return (
    <div className="flex items-center gap-3 px-3 py-2.5 bg-bg-secondary rounded-lg border border-border">
      {/* Preview thumbnail or icon */}
      <div className="w-10 h-10 rounded overflow-hidden bg-bg-tertiary flex-shrink-0 flex items-center justify-center">
        {item.preview ? (
          <img src={item.preview} alt="" className="w-full h-full object-cover" />
        ) : (
          <Image className="h-5 w-5 text-text-muted" />
        )}
      </div>

      {/* File info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm text-text-primary truncate">{item.file.name}</p>
          <span className="text-xs text-text-muted flex-shrink-0">{fileSizeMB} MB</span>
        </div>

        {/* Progress bar for uploading/processing */}
        {(item.status === 'uploading' || item.status === 'processing') && (
          <div className="mt-1.5">
            <ProgressBar
              value={item.status === 'processing' ? 100 : item.progress}
              size="sm"
            />
          </div>
        )}

        {/* Status text */}
        <p className={`text-xs mt-0.5 ${
          item.status === 'error' ? 'text-accent-danger' :
          item.status === 'complete' ? 'text-accent-success' :
          'text-text-muted'
        }`}>
          {statusText()}
        </p>
      </div>

      {/* Status icon */}
      {statusIcon()}
    </div>
  );
}
