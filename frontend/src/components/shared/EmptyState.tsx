import type { ReactNode } from 'react';

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
      {icon && <div className="mb-4 text-text-muted">{icon}</div>}
      <h3 className="text-lg font-medium text-text-primary mb-2">{title}</h3>
      {description && (
        <p className="text-sm text-text-secondary max-w-md mb-6">{description}</p>
      )}
      {action && <div>{action}</div>}
    </div>
  );
}
