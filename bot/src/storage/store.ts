import fs from 'fs';
import path from 'path';
import type { Account } from '../types.js';

const configuredDataDir = process.env.DATA_DIR?.trim();
const railwayVolumeDir = process.env.RAILWAY_VOLUME_MOUNT_PATH?.trim();
const DATA_DIR = path.resolve(configuredDataDir || railwayVolumeDir || path.join(process.cwd(), 'data'));
const DB_FILE = path.join(DATA_DIR, 'accounts.json');
const BACKUP_FILE = path.join(DATA_DIR, 'accounts.json.bak');

function ensureDir(): void {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
}

export function loadAccounts(): Account[] {
  ensureDir();
  if (!fs.existsSync(DB_FILE)) return [];
  try {
    return JSON.parse(fs.readFileSync(DB_FILE, 'utf8')) as Account[];
  } catch (error) {
    throw new Error(
      `تعذر قراءة ${DB_FILE}. لم تتم الكتابة فوق الملف حفاظًا على البيانات. ` +
      `يمكن استعادته من ${BACKUP_FILE} عند الحاجة. السبب: ${String(error)}`,
    );
  }
}

function save(accounts: Account[]): void {
  ensureDir();

  const tempFile = `${DB_FILE}.${process.pid}.tmp`;
  const serialized = JSON.stringify(accounts, null, 2);
  const fileDescriptor = fs.openSync(tempFile, 'w', 0o600);

  try {
    fs.writeFileSync(fileDescriptor, serialized, 'utf8');
    fs.fsyncSync(fileDescriptor);
  } finally {
    fs.closeSync(fileDescriptor);
  }

  if (fs.existsSync(DB_FILE)) {
    fs.copyFileSync(DB_FILE, BACKUP_FILE);
  }

  fs.renameSync(tempFile, DB_FILE);
  fs.chmodSync(DB_FILE, 0o600);
}

export function addAccount(data: Omit<Account, 'id' | 'createdAt' | 'updatedAt'>): Account {
  const accounts = loadAccounts();
  const now = new Date().toISOString();
  const acc: Account = { ...data, id: Date.now().toString(), createdAt: now, updatedAt: now };
  accounts.push(acc);
  save(accounts);
  return acc;
}

export function updateAccount(id: string, updates: Partial<Account>): Account | null {
  const accounts = loadAccounts();
  const idx = accounts.findIndex((a) => a.id === id);
  if (idx === -1) return null;
  accounts[idx] = { ...accounts[idx], ...updates, updatedAt: new Date().toISOString() };
  save(accounts);
  return accounts[idx];
}

export function deleteAccount(id: string): boolean {
  const accounts = loadAccounts();
  const filtered = accounts.filter((a) => a.id !== id);
  if (filtered.length === accounts.length) return false;
  save(filtered);
  return true;
}

export function getAccount(id: string): Account | null {
  return loadAccounts().find((a) => a.id === id) ?? null;
}
