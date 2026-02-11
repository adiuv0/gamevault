import { useEffect, useState } from 'react';
import {
  Settings,
  Key,
  Shield,
  HardDrive,
  Database,
  CheckCircle2,
  XCircle,
  Loader2,
  Eye,
  EyeOff,
  ExternalLink,
  Image,
  Gamepad2,
  MessageSquare,
  Share2,
  Download,
} from 'lucide-react';
import { getSettings, changePassword, saveApiKey, deleteApiKey } from '@/api/settings';
import type { AppSettings } from '@/api/settings';
import { Save, Trash2 } from 'lucide-react';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';

export function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Password change state
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [pwChanging, setPwChanging] = useState(false);
  const [pwResult, setPwResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      setLoading(true);
      const data = await getSettings();
      setSettings(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load settings');
    } finally {
      setLoading(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      setPwResult({ ok: false, message: 'New passwords do not match' });
      return;
    }
    if (newPassword.length < 6) {
      setPwResult({ ok: false, message: 'Password must be at least 6 characters' });
      return;
    }

    try {
      setPwChanging(true);
      setPwResult(null);
      await changePassword(currentPassword, newPassword);
      setPwResult({ ok: true, message: 'Password changed successfully' });
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to change password';
      setPwResult({ ok: false, message });
    } finally {
      setPwChanging(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error || !settings) {
    return (
      <div className="text-center py-12">
        <p className="text-accent-danger">{error || 'Failed to load settings'}</p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <Settings className="h-6 w-6 text-text-muted" />
        <h1 className="text-2xl font-bold text-text-primary">Settings</h1>
      </div>

      <div className="space-y-6 max-w-2xl">
        {/* Library Stats */}
        <section className="bg-bg-secondary border border-border rounded-lg p-6">
          <div className="flex items-center gap-2 mb-4">
            <Database className="h-5 w-5 text-accent-primary" />
            <h2 className="text-lg font-medium text-text-primary">Library Stats</h2>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            <StatCard icon={<Gamepad2 className="h-4 w-4" />} label="Games" value={settings.game_count} />
            <StatCard icon={<Image className="h-4 w-4" />} label="Screenshots" value={settings.screenshot_count} />
            <StatCard icon={<MessageSquare className="h-4 w-4" />} label="Annotations" value={settings.annotation_count} />
            <StatCard icon={<Share2 className="h-4 w-4" />} label="Active Shares" value={settings.active_share_count} />
            <StatCard icon={<Download className="h-4 w-4" />} label="Imports" value={settings.import_session_count} />
            <StatCard icon={<HardDrive className="h-4 w-4" />} label="Disk Usage" value={settings.library_size} />
          </div>
        </section>

        {/* Configuration */}
        <section className="bg-bg-secondary border border-border rounded-lg p-6">
          <div className="flex items-center gap-2 mb-4">
            <Settings className="h-5 w-5 text-accent-primary" />
            <h2 className="text-lg font-medium text-text-primary">Configuration</h2>
          </div>

          <div className="space-y-3 text-sm">
            <ConfigRow label="Base URL" value={settings.base_url} />
            <ConfigRow label="Library Path" value={settings.library_dir} />
            <ConfigRow label="Max Upload Size" value={`${settings.max_upload_size_mb} MB`} />
            <ConfigRow label="Thumbnail Quality" value={`${settings.thumbnail_quality}%`} />
            <ConfigRow label="Import Rate Limit" value={`${settings.import_rate_limit_ms}ms`} />
            <ConfigRow label="Token Expiry" value={`${settings.token_expiry_days} days`} />
            <ConfigRow
              label="Authentication"
              value={settings.auth_disabled ? 'Disabled' : 'Enabled'}
              highlight={settings.auth_disabled ? 'warning' : 'success'}
            />
          </div>

          <p className="mt-4 text-xs text-text-muted">
            Configuration is managed via environment variables (GAMEVAULT_*) in your
            docker-compose.yml or .env file.
          </p>
        </section>

        {/* API Keys */}
        <section className="bg-bg-secondary border border-border rounded-lg p-6">
          <div className="flex items-center gap-2 mb-4">
            <Key className="h-5 w-5 text-accent-primary" />
            <h2 className="text-lg font-medium text-text-primary">API Keys</h2>
          </div>
          <p className="text-sm text-text-secondary mb-4">
            API keys enhance metadata fetching for game covers, descriptions, and details.
            Save them here or set via environment variables.
          </p>

          <div className="space-y-3">
            <ApiKeyRow
              name="Steam Web API"
              keyName="steam_api_key"
              isSet={settings.has_steam_api_key}
              description="Fetches game metadata from the Steam Store API"
              docUrl="https://steamcommunity.com/dev/apikey"
              onSaved={loadSettings}
            />
            <ApiKeyRow
              name="SteamGridDB"
              keyName="steamgriddb_api_key"
              isSet={settings.has_steamgriddb_api_key}
              description="High-quality game cover art from the community"
              docUrl="https://www.steamgriddb.com/profile/preferences/api"
              onSaved={loadSettings}
            />
            <ApiKeyRow
              name="IGDB Client ID"
              keyName="igdb_client_id"
              isSet={settings.has_igdb_credentials}
              description="Twitch/IGDB client ID for non-Steam game metadata"
              docUrl="https://api-docs.igdb.com/#getting-started"
              onSaved={loadSettings}
            />
            <ApiKeyRow
              name="IGDB Client Secret"
              keyName="igdb_client_secret"
              isSet={settings.has_igdb_credentials}
              description="Twitch/IGDB client secret (paired with Client ID)"
              docUrl="https://api-docs.igdb.com/#getting-started"
              onSaved={loadSettings}
            />
          </div>
        </section>

        {/* Password Change */}
        {!settings.auth_disabled && (
          <section className="bg-bg-secondary border border-accent-danger/20 rounded-lg p-6">
            <div className="flex items-center gap-2 mb-4">
              <Shield className="h-5 w-5 text-accent-danger" />
              <h2 className="text-lg font-medium text-text-primary">Security</h2>
            </div>

            <form onSubmit={handleChangePassword} className="space-y-4">
              <div>
                <label className="block text-sm text-text-secondary mb-1.5">
                  Current Password
                </label>
                <div className="relative">
                  <input
                    type={showCurrent ? 'text' : 'password'}
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    className="w-full bg-bg-primary border border-border rounded-md px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-primary pr-10"
                    placeholder="Enter current password"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowCurrent(!showCurrent)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-text-muted hover:text-text-primary"
                  >
                    {showCurrent ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-sm text-text-secondary mb-1.5">
                  New Password
                </label>
                <div className="relative">
                  <input
                    type={showNew ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="w-full bg-bg-primary border border-border rounded-md px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-primary pr-10"
                    placeholder="Enter new password (min 6 chars)"
                    required
                    minLength={6}
                  />
                  <button
                    type="button"
                    onClick={() => setShowNew(!showNew)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-text-muted hover:text-text-primary"
                  >
                    {showNew ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-sm text-text-secondary mb-1.5">
                  Confirm New Password
                </label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full bg-bg-primary border border-border rounded-md px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-primary"
                  placeholder="Re-enter new password"
                  required
                  minLength={6}
                />
              </div>

              {pwResult && (
                <div
                  className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm ${
                    pwResult.ok
                      ? 'bg-accent-success/10 text-accent-success border border-accent-success/20'
                      : 'bg-accent-danger/10 text-accent-danger border border-accent-danger/20'
                  }`}
                >
                  {pwResult.ok ? (
                    <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
                  ) : (
                    <XCircle className="h-4 w-4 flex-shrink-0" />
                  )}
                  {pwResult.message}
                </div>
              )}

              <button
                type="submit"
                disabled={pwChanging || !currentPassword || !newPassword || !confirmPassword}
                className="flex items-center gap-2 px-4 py-2 bg-accent-danger text-white rounded-md text-sm font-medium hover:bg-accent-danger/90 transition-colors disabled:opacity-50"
              >
                {pwChanging && <Loader2 className="h-4 w-4 animate-spin" />}
                Change Password
              </button>
            </form>
          </section>
        )}

        {/* About */}
        <section className="bg-bg-secondary border border-border rounded-lg p-6">
          <h2 className="text-lg font-medium text-text-primary mb-2">About GameVault</h2>
          <p className="text-sm text-text-secondary">
            Self-hosted game screenshot manager. Organize, annotate, and share your
            gaming memories.
          </p>
          <p className="text-xs text-text-muted mt-2">
            Built with FastAPI + React + SQLite
          </p>
        </section>
      </div>
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function StatCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
}) {
  return (
    <div className="bg-bg-primary border border-border rounded-lg p-3">
      <div className="flex items-center gap-2 text-text-muted mb-1">
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <p className="text-lg font-semibold text-text-primary">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
    </div>
  );
}

function ConfigRow({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: 'success' | 'warning';
}) {
  const valueColor = highlight === 'success'
    ? 'text-accent-success'
    : highlight === 'warning'
      ? 'text-accent-warning'
      : 'text-text-primary';

  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border/50 last:border-0">
      <span className="text-text-secondary">{label}</span>
      <span className={`font-mono text-xs ${valueColor}`}>{value}</span>
    </div>
  );
}

function ApiKeyRow({
  name,
  keyName,
  isSet,
  description,
  docUrl,
  onSaved,
}: {
  name: string;
  keyName: string;
  isSet: boolean;
  description: string;
  docUrl: string;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const handleSave = async () => {
    if (!value.trim()) return;
    try {
      setSaving(true);
      setResult(null);
      await saveApiKey(keyName, value.trim());
      setResult({ ok: true, message: 'Saved' });
      setValue('');
      setEditing(false);
      onSaved();
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : 'Failed to save' });
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async () => {
    try {
      setDeleting(true);
      setResult(null);
      await deleteApiKey(keyName);
      setResult({ ok: true, message: 'Removed' });
      onSaved();
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : 'Failed to remove' });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="p-3 bg-bg-primary border border-border rounded-lg">
      <div className="flex items-start gap-3">
        <div className="flex-shrink-0 mt-0.5">
          {isSet ? (
            <CheckCircle2 className="h-5 w-5 text-accent-success" />
          ) : (
            <XCircle className="h-5 w-5 text-text-muted" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-text-primary">{name}</span>
            {isSet ? (
              <span className="text-xs px-1.5 py-0.5 bg-accent-success/10 text-accent-success rounded">
                Connected
              </span>
            ) : (
              <span className="text-xs px-1.5 py-0.5 bg-bg-tertiary text-text-muted rounded">
                Not Set
              </span>
            )}
          </div>
          <p className="text-xs text-text-secondary mt-0.5">{description}</p>
          <div className="flex items-center gap-3 mt-1.5">
            <a
              href={docUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-accent-primary hover:text-accent-primary/80 flex items-center gap-1"
            >
              Get Key <ExternalLink className="h-3 w-3" />
            </a>
            {!editing && !isSet && (
              <button
                onClick={() => setEditing(true)}
                className="text-xs text-accent-primary hover:text-accent-primary/80"
              >
                Add Key
              </button>
            )}
            {!editing && isSet && (
              <>
                <button
                  onClick={() => setEditing(true)}
                  className="text-xs text-accent-primary hover:text-accent-primary/80"
                >
                  Update
                </button>
                <button
                  onClick={handleRemove}
                  disabled={deleting}
                  className="text-xs text-accent-danger hover:text-accent-danger/80 flex items-center gap-1"
                >
                  {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                  Remove
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {editing && (
        <div className="mt-3 flex items-center gap-2">
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={`Enter ${name} key...`}
            className="flex-1 bg-bg-tertiary border border-border rounded-md px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-primary font-mono"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSave();
              if (e.key === 'Escape') { setEditing(false); setValue(''); }
            }}
          />
          <button
            onClick={handleSave}
            disabled={saving || !value.trim()}
            className="flex items-center gap-1 px-3 py-1.5 bg-accent-primary text-white rounded-md text-xs font-medium hover:bg-accent-primary/90 disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            Save
          </button>
          <button
            onClick={() => { setEditing(false); setValue(''); setResult(null); }}
            className="px-3 py-1.5 text-text-muted hover:text-text-primary text-xs"
          >
            Cancel
          </button>
        </div>
      )}

      {result && (
        <div className={`mt-2 text-xs px-2 py-1 rounded ${
          result.ok
            ? 'text-accent-success bg-accent-success/10'
            : 'text-accent-danger bg-accent-danger/10'
        }`}>
          {result.message}
        </div>
      )}
    </div>
  );
}
