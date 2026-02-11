import { useCallback, useState, useRef } from 'react';
import { Upload, ImagePlus } from 'lucide-react';
import { ACCEPTED_IMAGE_TYPES } from '@/lib/constants';

interface UploadZoneProps {
  onFilesSelected: (files: File[]) => void;
  disabled?: boolean;
}

const ACCEPTED_EXTENSIONS = Object.values(ACCEPTED_IMAGE_TYPES).flat();
const ACCEPTED_MIME = Object.keys(ACCEPTED_IMAGE_TYPES).join(',');

export function UploadZone({ onFilesSelected, disabled = false }: UploadZoneProps) {
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (!disabled) setIsDragOver(true);
  }, [disabled]);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (disabled) return;

    const files = Array.from(e.dataTransfer.files).filter((f) =>
      ACCEPTED_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)),
    );
    if (files.length > 0) {
      onFilesSelected(files);
    }
  }, [disabled, onFilesSelected]);

  const handleClick = () => {
    if (!disabled) {
      inputRef.current?.click();
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) {
      onFilesSelected(files);
    }
    // Reset so same files can be re-selected
    e.target.value = '';
  };

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onClick={handleClick}
      className={`
        border-2 border-dashed rounded-lg p-12 text-center cursor-pointer transition-all duration-200
        ${disabled
          ? 'border-border/50 bg-bg-secondary/50 cursor-not-allowed opacity-60'
          : isDragOver
            ? 'border-accent-primary bg-accent-primary/5 scale-[1.01]'
            : 'border-border hover:border-accent-primary/50 hover:bg-bg-secondary/50'
        }
      `}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPTED_MIME}
        onChange={handleInputChange}
        className="hidden"
      />

      <div className="flex flex-col items-center gap-3">
        {isDragOver ? (
          <ImagePlus className="h-12 w-12 text-accent-primary" />
        ) : (
          <Upload className="h-12 w-12 text-text-muted" />
        )}

        <div>
          <p className="text-sm text-text-primary font-medium">
            {isDragOver
              ? 'Drop your screenshots here'
              : 'Drag and drop screenshots here, or click to browse'}
          </p>
          <p className="text-xs text-text-muted mt-1">
            Supports JPG, PNG, WebP, BMP, TIFF — up to 50 MB per file
          </p>
        </div>
      </div>
    </div>
  );
}
