import { ArrowUpDown } from 'lucide-react';

export type ScreenshotSort = 'date_desc' | 'date_asc';

interface ScreenshotSortControlProps {
  value: ScreenshotSort;
  onChange: (sort: ScreenshotSort) => void;
}

export function ScreenshotSortControl({
  value,
  onChange,
}: ScreenshotSortControlProps) {
  return (
    <div className="flex justify-end mb-3">
      <div className="relative">
        <ArrowUpDown
          className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted pointer-events-none"
          aria-hidden="true"
        />
        <select
          value={value}
          onChange={(event) => onChange(event.target.value as ScreenshotSort)}
          aria-label="Sort screenshots"
          className="h-9 w-40 pl-9 pr-3 bg-bg-secondary border border-border rounded-md text-sm text-text-primary focus:outline-none focus:border-accent-primary"
        >
          <option value="date_desc">Newest first</option>
          <option value="date_asc">Oldest first</option>
        </select>
      </div>
    </div>
  );
}
