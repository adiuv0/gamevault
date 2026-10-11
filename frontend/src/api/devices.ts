import { api } from './client';

/** A PC signed in to the GameVault Sync app (or a token made for a script). */
export interface SyncDevice {
  id: number;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

const RELEASES = 'https://github.com/adiuv0/gamevault/releases/latest';
export const SYNC_APP_DOWNLOADS = {
  installer: `${RELEASES}/download/GameVaultSync-Setup.exe`,
  portable: `${RELEASES}/download/GameVaultSync.exe`,
  readme: 'https://github.com/adiuv0/gamevault/tree/main/desktop#readme',
};

// SQLite writes UTC as "YYYY-MM-DD HH:MM:SS"; make it an ISO string browsers read as UTC.
function utc(value: string | null): string | null {
  return value ? `${value.replace(' ', 'T')}Z` : null;
}

function normalize(device: SyncDevice): SyncDevice {
  return { ...device, created_at: utc(device.created_at) ?? device.created_at, last_used_at: utc(device.last_used_at) };
}

export async function listDevices(): Promise<SyncDevice[]> {
  const { devices } = await api.get<{ devices: SyncDevice[] }>('/auth/devices');
  return devices.map(normalize);
}

export async function createDeviceToken(name: string): Promise<{ token: string; device: SyncDevice }> {
  const result = await api.post<{ token: string; device: SyncDevice }>('/auth/devices', { name });
  return { ...result, device: normalize(result.device) };
}

export async function revokeDevice(id: number): Promise<void> {
  await api.delete(`/auth/devices/${id}`);
}
