import { useEffect, useRef, type ReactNode } from 'react';
import { Loader2 } from 'lucide-react';

export interface ConfirmAction {
  label: string;
  onClick: () => void;
  /** The red, focused action: Enter confirms it. */
  danger?: boolean;
}

interface ConfirmDialogProps {
  title: string;
  children: ReactNode;
  actions: ConfirmAction[];
  onCancel: () => void;
  busy?: boolean;
  error?: string | null;
}

/** A small modal that sits above the viewer. Escape or a click outside cancels. */
export function ConfirmDialog({ title, children, actions, onCancel, busy, error }: ConfirmDialogProps) {
  const primary = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    primary.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || busy) return;
      // Handled here only: the viewer underneath must not close too.
      event.preventDefault();
      event.stopImmediatePropagation();
      onCancel();
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [busy, onCancel]);

  const firstDanger = actions.findIndex((action) => action.danger);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/60" onClick={busy ? undefined : onCancel} />
      <div className="relative bg-bg-secondary border border-border rounded-lg p-5 w-full max-w-md shadow-xl">
        <h2 className="text-lg font-semibold text-text-primary mb-2">{title}</h2>
        <div className="text-sm text-text-secondary mb-4 space-y-2">{children}</div>
        {error && (
          <p role="alert" className="mb-3 px-3 py-2 bg-accent-danger/10 border border-accent-danger/30 rounded text-xs text-accent-danger">
            {error}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="px-4 py-2 bg-bg-tertiary text-text-primary rounded-md text-sm hover:bg-bg-tertiary/80 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          {actions.map((action, index) => (
            <button
              key={action.label}
              ref={index === firstDanger ? primary : undefined}
              type="button"
              onClick={action.onClick}
              disabled={busy}
              className={action.danger
                ? 'flex items-center gap-2 px-4 py-2 bg-accent-danger text-white rounded-md text-sm font-medium hover:bg-accent-danger/90 transition-colors disabled:opacity-50'
                : 'px-4 py-2 border border-accent-danger/40 text-accent-danger rounded-md text-sm hover:bg-accent-danger/10 transition-colors disabled:opacity-50'}
            >
              {busy && index === firstDanger && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {action.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
