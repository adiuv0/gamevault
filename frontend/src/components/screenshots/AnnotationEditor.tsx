import { useState, useEffect } from 'react';
import { Save, X, Edit3, Trash2, Loader2 } from 'lucide-react';
import { getAnnotation, saveAnnotation, deleteAnnotation } from '@/api/screenshots';

// Session-only drafts survive pane toggles and navigating away from a capture.
const drafts = new Map<number, string>();

interface AnnotationEditorProps {
  screenshotId: number;
  hasAnnotation: boolean;
  onAnnotationChanged?: (hasAnnotation: boolean) => void;
}

export function AnnotationEditor({
  screenshotId,
  hasAnnotation,
  onAnnotationChanged,
}: AnnotationEditorProps) {
  const [mode, setMode] = useState<'view' | 'edit'>(drafts.has(screenshotId) ? 'edit' : 'view');
  const [content, setContent] = useState(drafts.get(screenshotId) ?? '');
  const [contentHtml, setContentHtml] = useState('');
  const [originalContent, setOriginalContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    if (!hasAnnotation) {
      setContent(drafts.get(screenshotId) ?? '');
      setContentHtml('');
      setOriginalContent('');
      return;
    }

    const loadAnnotation = async () => {
      try {
        setLoading(true);
        const annotation = await getAnnotation(screenshotId);
        if (annotation && !cancelled) {
          setContent(drafts.get(screenshotId) ?? annotation.content);
          setContentHtml(annotation.content_html || '');
          setOriginalContent(annotation.content);
        }
      } catch {
        if (!cancelled) setError('Could not load annotation. Your draft has been kept.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void loadAnnotation();
    return () => {
      cancelled = true;
    };
  }, [screenshotId, hasAnnotation]);

  const handleSave = async () => {
    if (!content.trim()) return;
    try {
      setSaving(true);
      setError(null);
      const result = await saveAnnotation(screenshotId, content);
      drafts.delete(screenshotId);
      setContentHtml(result.content_html || '');
      setOriginalContent(content);
      setMode('view');
      onAnnotationChanged?.(true);
    } catch {
      setError('Could not save annotation. Your draft has been kept.');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    try {
      setDeleting(true);
      setError(null);
      await deleteAnnotation(screenshotId);
      drafts.delete(screenshotId);
      setContent('');
      setContentHtml('');
      setOriginalContent('');
      setMode('view');
      onAnnotationChanged?.(false);
    } catch {
      setError('Could not delete annotation. Please try again.');
    } finally {
      setDeleting(false);
    }
  };

  const handleCancel = () => {
    drafts.delete(screenshotId);
    setContent(originalContent);
    setMode('view');
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-text-muted py-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading annotation...
      </div>
    );
  }

  // Edit mode
  if (mode === 'edit') {
    return (
      <div className="space-y-2">
        {error && <p role="alert" className="text-sm text-accent-danger">{error}</p>}
        <textarea
          value={content}
          disabled={saving || deleting}
          onChange={(e) => { drafts.set(screenshotId, e.target.value); setContent(e.target.value); }}
          placeholder="Write your annotation... (supports Markdown)"
          className="w-full h-32 px-3 py-2 bg-bg-primary border border-border rounded-md text-sm text-text-primary placeholder-text-muted resize-y focus:outline-none focus:border-accent-primary"
          autoFocus
        />
        <p className="text-xs text-text-muted">
          Supports **bold**, *italic*, `code`, lists, and more Markdown syntax.
        </p>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {originalContent && (
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="flex items-center gap-1 px-2 py-1 text-xs text-accent-danger hover:bg-accent-danger/10 rounded transition-colors"
              >
                {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                Delete
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleCancel}
              disabled={saving || deleting}
              className="flex items-center gap-1 px-3 py-1 text-xs text-text-secondary hover:text-text-primary rounded transition-colors"
            >
              <X className="h-3 w-3" />
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={!content.trim() || saving}
              className="flex items-center gap-1 px-3 py-1 text-xs bg-accent-primary text-white rounded hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
            >
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
              Save
            </button>
          </div>
        </div>
      </div>
    );
  }

  // View mode - no annotation
  if (!hasAnnotation || !originalContent) {
    return (
      <div>
      {error && <p role="alert" className="text-sm text-accent-danger">{error}</p>}
      <button
        onClick={() => setMode('edit')}
        className="flex items-center gap-2 text-sm text-text-muted hover:text-accent-primary transition-colors py-1"
      >
        <Edit3 className="h-4 w-4" />
        Add annotation...
      </button>
      </div>
    );
  }

  // View mode - has annotation
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-text-muted uppercase tracking-wider">Annotation</span>
        <button
          onClick={() => setMode('edit')}
          className="p-1 text-text-muted hover:text-accent-primary transition-colors"
          title="Edit annotation"
        >
          <Edit3 className="h-3 w-3" />
        </button>
      </div>
      {contentHtml ? (
        <div
          className="prose prose-sm prose-invert max-w-none text-text-secondary"
          dangerouslySetInnerHTML={{ __html: contentHtml }}
        />
      ) : (
        <p className="text-sm text-text-secondary whitespace-pre-wrap">{originalContent}</p>
      )}
    </div>
  );
}
