"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.loadAccounts = loadAccounts;
exports.addAccount = addAccount;
exports.updateAccount = updateAccount;
exports.deleteAccount = deleteAccount;
exports.getAccount = getAccount;
const fs_1 = __importDefault(require("fs"));
const path_1 = __importDefault(require("path"));
const DATA_DIR = process.env.DATA_DIR ?? path_1.default.join(process.cwd(), 'data');
const DB_FILE = path_1.default.join(DATA_DIR, 'accounts.json');
function ensureDir() {
    if (!fs_1.default.existsSync(DATA_DIR))
        fs_1.default.mkdirSync(DATA_DIR, { recursive: true });
}
function loadAccounts() {
    ensureDir();
    if (!fs_1.default.existsSync(DB_FILE))
        return [];
    try {
        return JSON.parse(fs_1.default.readFileSync(DB_FILE, 'utf8'));
    }
    catch {
        return [];
    }
}
function save(accounts) {
    ensureDir();
    fs_1.default.writeFileSync(DB_FILE, JSON.stringify(accounts, null, 2), 'utf8');
}
function addAccount(data) {
    const accounts = loadAccounts();
    const now = new Date().toISOString();
    const acc = { ...data, id: Date.now().toString(), createdAt: now, updatedAt: now };
    accounts.push(acc);
    save(accounts);
    return acc;
}
function updateAccount(id, updates) {
    const accounts = loadAccounts();
    const idx = accounts.findIndex((a) => a.id === id);
    if (idx === -1)
        return null;
    accounts[idx] = { ...accounts[idx], ...updates, updatedAt: new Date().toISOString() };
    save(accounts);
    return accounts[idx];
}
function deleteAccount(id) {
    const accounts = loadAccounts();
    const filtered = accounts.filter((a) => a.id !== id);
    if (filtered.length === accounts.length)
        return false;
    save(filtered);
    return true;
}
function getAccount(id) {
    return loadAccounts().find((a) => a.id === id) ?? null;
}
