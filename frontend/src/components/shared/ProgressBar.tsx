interface ProgressBarProps {
  value: number; // 0-100
  label?: string;
  size?: 'sm' | 'md';
  variant?: 'primary' | 'success' | 'warning';
}

export function ProgressBar({
  value,
  label,
  size = 'md',
  variant = 'primary',
}: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const heightClass = size === 'sm' ? 'h-1.5' : 'h-2.5';
  const colorClass = {
    primary: 'bg-accent-primary',
    success: 'bg-accent-success',
    warning: 'bg-accent-warning',
  }[variant];

  return (
    <div className="w-full">
      {label && (
        <div className="flex justify-between mb-1">
          <span className="text-xs text-text-secondary">{label}</span>
          <span className="text-xs text-text-muted">{Math.round(clamped)}%</span>
        </div>
      )}
      <div className={`w-full ${heightClass} bg-bg-tertiary rounded-full overflow-hidden`}>
        <div
          className={`${heightClass} ${colorClass} rounded-full transition-all duration-300 ease-out`}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  );
}
