import fs from 'fs';
import path from 'path';
import { encrypt, decrypt } from '../utils/crypto.js';
import type { Account } from '../types.js';

const DATA_DIR = process.env.DATA_DIR ?? path.join(process.cwd(), 'data');
const DB_FILE = path.join(DATA_DIR, 'accounts.enc');

function ensureDir(): void {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
}

export function loadAccounts(): Account[] {
  ensureDir();
  if (!fs.existsSync(DB_FILE)) return [];
  try {
    const raw = fs.readFileSync(DB_FILE, 'utf8');
    return JSON.parse(decrypt(raw)) as Account[];
  } catch {
    return [];
  }
}

function save(accounts: Account[]): void {
  ensureDir();
  fs.writeFileSync(DB_FILE, encrypt(JSON.stringify(accounts, null, 2)), 'utf8');
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
