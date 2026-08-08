export interface Account {
  id: string;
  name?: string;
  email: string;
  password: string;
  totpSecret: string;
  recoveryCodes: string[];
  appPassword: string;
  createdAt: string;
  updatedAt: string;
}

export type AddStep =
  | 'add_email'
  | 'add_password'
  | 'add_totp'
  | 'add_appPassword';

export type EditStep = 'edit_entering_value';

export interface SessionData {
  step?: AddStep | EditStep;
  pendingAccount?: Partial<Account>;
  editingId?: string;
  editField?: keyof Pick<Account, 'email' | 'password' | 'totpSecret' | 'recoveryCodes' | 'appPassword'>;
}
