import { type ReactNode, useEffect, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Link, Route, Switch, useLocation, useParams, Router as WouterRouter } from 'wouter';
import {
  Activity, ArrowLeft, ArrowRight, BookOpen, Check, CheckCircle2, ChevronLeft,
  ChevronRight, CircleHelp, ClipboardCheck, Clock3, Copy, Eye, EyeOff, FileKey2,
  KeyRound, LayoutDashboard, LifeBuoy, LockKeyhole, Mail, Menu, Pencil, Plus,
  RefreshCcw, Search, Server, ShieldCheck, SlidersHorizontal, Trash2, UserRound,
  X, Zap
} from 'lucide-react';

const queryClient = new QueryClient();
const STORAGE_KEY = 'bot-adjuster.accounts.v1';

type AccountStatus = 'verified' | 'pending' | 'attention';
type Account = {
  id: string; email: string; password: string; totpSecret: string; appPassword: string;
  recoveryCodes: string; status: AccountStatus; lastVerifiedAt: string | null; createdAt: string;
};

const seedAccounts: Account[] = [
  { id: 'acc-marsa', email: 'marsa.support@gmail.com', password: 'Marsa!2024', totpSecret: 'JBSWY3DPEHPK3PXP', appPassword: 'wqne bpxz vjtm krfd', recoveryCodes: '142893 761204 309118 884507', status: 'verified', lastVerifiedAt: '2026-08-07T17:45:00.000Z', createdAt: '2026-07-26T09:10:00.000Z' },
  { id: 'acc-north', email: 'northdesk@outlook.com', password: 'NorthDesk#8', totpSecret: 'KRSXG5A2L5XXE3DE', appPassword: 'azur fnda pkqe mtuy', recoveryCodes: '682114 094321 552900 143762', status: 'pending', lastVerifiedAt: null, createdAt: '2026-08-02T12:30:00.000Z' },
  { id: 'acc-ops', email: 'ops.almanar@gmail.com', password: 'Almanar-ops-17', totpSecret: 'NBSWY3DPEB3W64TM', appPassword: 'rjlf mmqu ezdb cwpa', recoveryCodes: '902315 238841 117650 664902', status: 'attention', lastVerifiedAt: '2026-07-30T08:20:00.000Z', createdAt: '2026-07-12T08:20:00.000Z' },
];

function getInitialAccounts(): Account[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return JSON.parse(stored) as Account[];
  } catch { /* safe fallback */ }
  return seedAccounts;
}

function App() {
  const [accounts, setAccounts] = useState<Account[]>(getInitialAccounts);
  useEffect(() => localStorage.setItem(STORAGE_KEY, JSON.stringify(accounts)), [accounts]);
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
          <AppShell accounts={accounts} setAccounts={setAccounts} />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

function AppShell({ accounts, setAccounts }: { accounts: Account[]; setAccounts: (a: Account[]) => void }) {
  const [location, setLocation] = useLocation();
  const [mobileNav, setMobileNav] = useState(false);
  const nav = [
    { href: '/', label: 'نظرة عامة', icon: LayoutDashboard },
    { href: '/accounts', label: 'الحسابات', icon: Server },
    { href: '/guide', label: 'الدليل السريع', icon: BookOpen },
  ];
  return (
    <div dir="rtl" className="min-h-[100dvh] bg-background text-foreground">
      <header className="sticky top-0 z-30 flex h-[72px] items-center justify-between border-b border-border/80 bg-background/90 px-4 backdrop-blur-xl lg:hidden">
        <button data-testid="button-mobile-menu" onClick={() => setMobileNav(!mobileNav)} className="grid h-10 w-10 place-items-center rounded-xl border border-border bg-card text-muted-foreground" aria-label="فتح القائمة">
          {mobileNav ? <X size={19} /> : <Menu size={19} />}
        </button>
        <Link href="/" data-testid="link-mobile-brand" className="flex items-center gap-2.5">
          <Mark />
          <span className="font-semibold tracking-tight">Bot Adjuster</span>
        </Link>
        <Link href="/accounts/new" data-testid="link-mobile-add" className="grid h-10 w-10 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm"><Plus size={19} /></Link>
      </header>
      {mobileNav && <div onClick={() => setMobileNav(false)} className="fixed inset-0 z-20 bg-foreground/15 lg:hidden" />}
      <div className="flex min-h-[calc(100dvh-72px)] lg:min-h-[100dvh]">
        <aside className={`${mobileNav ? 'translate-x-0' : 'translate-x-full'} fixed right-0 top-[72px] z-30 flex h-[calc(100dvh-72px)] w-[285px] flex-col border-l border-border bg-card px-4 py-5 transition-transform duration-300 lg:sticky lg:top-0 lg:h-[100dvh] lg:w-[276px] lg:translate-x-0 lg:shrink-0 lg:px-5`}>
          <div className="mb-8 hidden items-center gap-3 px-2 lg:flex">
            <Mark />
            <div><div className="font-semibold tracking-tight">Bot Adjuster</div><div className="mt-0.5 text-[10px] uppercase tracking-[.18em] text-muted-foreground">owner control room</div></div>
          </div>
          <div className="mb-5 px-2 text-[11px] font-semibold tracking-[.18em] text-muted-foreground">مساحة العمل</div>
          <nav className="space-y-1.5">
            {nav.map(({ href, label, icon: Icon }) => <Link key={href} href={href} onClick={() => setMobileNav(false)} data-testid={`link-nav-${label}`} className={`flex items-center gap-3 rounded-xl px-3.5 py-3 text-sm transition-colors ${location === href ? 'bg-primary text-primary-foreground shadow-[0_8px_20px_hsl(var(--primary)/.16)]' : 'text-muted-foreground hover:bg-secondary hover:text-foreground'}`}><Icon size={18} strokeWidth={location === href ? 2.25 : 1.8} /><span>{label}</span>{href === '/accounts' && <span className={`mr-auto rounded-md px-2 py-0.5 text-[11px] font-mono ${location === href ? 'bg-primary-foreground/15 text-primary-foreground' : 'bg-muted text-muted-foreground'}`}>{accounts.length}</span>}</Link>)}
          </nav>
          <div className="my-7 h-px bg-border" />
          <div className="mb-3 px-2 text-[11px] font-semibold tracking-[.18em] text-muted-foreground">معلومات</div>
          <div className="space-y-1.5">
            <div className="flex items-center gap-3 rounded-xl px-3.5 py-3 text-sm text-muted-foreground"><LockKeyhole size={18} /><span>محلي ومشفّر بصرياً</span></div>
            <div className="flex items-center gap-3 rounded-xl px-3.5 py-3 text-sm text-muted-foreground"><Activity size={18} /><span>آخر مزامنة منذ لحظات</span></div>
          </div>
          <div className="mt-auto rounded-2xl border border-border bg-secondary/55 p-4">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold"><ShieldCheck size={16} className="text-primary" /> مساحة المالك</div>
            <p className="text-xs leading-6 text-muted-foreground">بيانات الحسابات محفوظة على هذا الجهاز فقط. لا نرسل الأسرار إلى أي خدمة.</p>
          </div>
        </aside>
        <main className="min-w-0 flex-1">
          <Switch>
            <Route path="/" component={() => <Dashboard accounts={accounts} />} />
            <Route path="/accounts/new" component={() => <AccountForm mode="new" accounts={accounts} setAccounts={setAccounts} />} />
            <Route path="/accounts/:id" component={() => <AccountForm mode="edit" accounts={accounts} setAccounts={setAccounts} />} />
            <Route path="/accounts" component={() => <AccountsPage accounts={accounts} setAccounts={setAccounts} />} />
            <Route path="/guide" component={Guide} />
            <Route component={NotFound} />
          </Switch>
        </main>
      </div>
    </div>
  );
}

function Mark() {
  return <div className="relative grid h-10 w-10 place-items-center overflow-hidden rounded-xl bg-primary text-primary-foreground shadow-[0_7px_16px_hsl(var(--primary)/.2)]"><Zap size={19} fill="currentColor" strokeWidth={1.8} /><span className="absolute -bottom-2 -left-1 h-5 w-5 rounded-full bg-accent/80" /></div>;
}

function PageHead({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <div className="mb-8 flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
    <div><div className="mb-3 flex items-center gap-2 text-[11px] font-semibold tracking-[.2em] text-primary"><span className="h-1.5 w-1.5 rounded-full bg-accent" /> {eyebrow}</div><h1 className="font-arabic text-2xl font-semibold tracking-tight text-foreground sm:text-[30px]">{title}</h1><p className="mt-2 max-w-xl text-sm leading-7 text-muted-foreground">{description}</p></div>
    {action}
  </div>;
}

function Stat({ icon: Icon, label, value, note, tone = 'teal' }: { icon: typeof Server; label: string; value: string; note: string; tone?: 'teal' | 'coral' | 'sand' }) {
  const colors = { teal: 'bg-primary/10 text-primary', coral: 'bg-accent/12 text-accent', sand: 'bg-secondary text-foreground' };
  return <div className="rounded-2xl border border-border bg-card p-4 shadow-[0_2px_10px_hsl(var(--foreground)/.025)] sm:p-5"><div className="flex items-start justify-between"><div className={`grid h-10 w-10 place-items-center rounded-xl ${colors[tone]}`}><Icon size={19} /></div><span className="text-[11px] text-muted-foreground">{note}</span></div><div className="mt-5 font-mono-app text-2xl font-medium tracking-tight">{value}</div><div className="mt-1 text-xs text-muted-foreground">{label}</div></div>;
}

function Dashboard({ accounts }: { accounts: Account[] }) {
  const verified = accounts.filter(a => a.status === 'verified').length;
  const pending = accounts.filter(a => a.status !== 'verified').length;
  return <div className="surface-grid min-h-full px-4 py-7 sm:px-8 sm:py-10 xl:px-12">
    <div className="mx-auto max-w-6xl">
      <PageHead eyebrow="BOT ADJUSTER / اليوم" title="مرحباً بك في غرفة التحكم" description="كل حسابات البوت في مكان واحد. راجع الحالة، حدّث بيانات الوصول، وتحقق من البريد بخطوات واضحة." action={<Link href="/accounts/new" data-testid="link-add-account-dashboard" className="group inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground shadow-[0_9px_22px_hsl(var(--primary)/.18)] transition-transform hover:-translate-y-0.5"><Plus size={17} /> إضافة حساب <ArrowLeft size={15} className="transition-transform group-hover:-translate-x-1" /></Link>} />
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat icon={Server} label="إجمالي الحسابات" value={String(accounts.length).padStart(2, '0')} note="حسابات محفوظة" />
        <Stat icon={ShieldCheck} label="حسابات موثقة" value={String(verified).padStart(2, '0')} note="جاهزة للاستخدام" tone="teal" />
        <Stat icon={Clock3} label="تحتاج مراجعة" value={String(pending).padStart(2, '0')} note="مهمتك التالية" tone="coral" />
      </div>
      <div className="mt-8 grid gap-5 xl:grid-cols-[1.35fr_.65fr]">
        <section className="animate-rise rounded-2xl border border-border bg-card p-5 sm:p-6">
          <div className="mb-5 flex items-center justify-between"><div><h2 className="font-arabic font-semibold">الحسابات الأخيرة</h2><p className="mt-1 text-xs text-muted-foreground">نظرة سريعة على مساحة الحسابات</p></div><Link href="/accounts" data-testid="link-see-all-accounts" className="flex items-center gap-1 text-xs font-semibold text-primary hover:underline">عرض الكل <ChevronLeft size={15} /></Link></div>
          <div className="space-y-2.5">{accounts.slice(0, 3).map(account => <AccountRow key={account.id} account={account} />)}</div>
          {!accounts.length && <EmptyState compact />}
        </section>
        <section className="animate-rise animate-rise-delay-1 relative overflow-hidden rounded-2xl bg-primary p-6 text-primary-foreground">
          <div className="absolute -left-12 -top-16 h-40 w-40 rounded-full border-[22px] border-primary-foreground/10" /><div className="absolute -bottom-20 -right-14 h-48 w-48 rounded-full border-[28px] border-accent/30" />
          <div className="relative"><div className="mb-10 grid h-10 w-10 place-items-center rounded-xl bg-primary-foreground/12"><BookOpen size={19} /></div><h2 className="font-arabic text-xl font-semibold leading-9">تحتاج مساعدة في<br />إضافة حساب جديد؟</h2><p className="mt-3 text-sm leading-7 text-primary-foreground/70">الدليل يشرح كل خطوة بدون تعقيد، من البريد إلى التحقق.</p><Link href="/guide" data-testid="link-read-guide-dashboard" className="mt-7 inline-flex items-center gap-2 rounded-xl bg-primary-foreground px-4 py-3 text-xs font-semibold text-primary transition-transform hover:-translate-y-0.5">افتح الدليل <ArrowLeft size={15} /></Link></div>
        </section>
      </div>
      <div className="mt-5 rounded-2xl border border-border bg-card p-5 sm:p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"><div><div className="flex items-center gap-2 text-sm font-semibold"><ClipboardCheck size={17} className="text-primary" /> سير العمل المقترح</div><p className="mt-1 text-xs text-muted-foreground">ثلاث خطوات بسيطة للحفاظ على الحسابات جاهزة.</p></div><div className="flex flex-wrap items-center gap-2 text-xs"><WorkflowStep number="01" label="أضف البيانات" active={accounts.length === 0} /><ChevronLeft size={14} className="text-border" /><WorkflowStep number="02" label="تحقق من البريد" active={pending > 0} /><ChevronLeft size={14} className="text-border" /><WorkflowStep number="03" label="جاهز" active={pending === 0 && accounts.length > 0} /></div></div>
      </div>
    </div>
  </div>;
}

function WorkflowStep({ number, label, active }: { number: string; label: string; active: boolean }) {
  return <div className={`flex items-center gap-2 rounded-lg px-2.5 py-2 ${active ? 'bg-accent/10 text-accent' : 'text-muted-foreground'}`}><span className="font-mono-app text-[10px]">{number}</span><span>{label}</span>{active && <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse-soft" />}</div>;
}

function AccountRow({ account }: { account: Account }) {
  return <Link href={`/accounts/${account.id}`} data-testid={`link-account-${account.id}`} className="group flex items-center gap-3 rounded-xl border border-transparent p-3 transition-colors hover:border-border hover:bg-secondary/50">
    <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-secondary font-mono-app text-sm font-medium text-primary">{account.email.slice(0, 2).toUpperCase()}</div>
    <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{account.email}</div><div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground"><span className={`h-1.5 w-1.5 rounded-full ${account.status === 'verified' ? 'bg-primary' : account.status === 'pending' ? 'bg-accent' : 'bg-destructive'}`} />{statusLabel(account.status)}</div></div>
    <ChevronLeft size={16} className="text-muted-foreground transition-transform group-hover:-translate-x-1" />
  </Link>;
}

function statusLabel(status: AccountStatus) { return status === 'verified' ? 'موثق' : status === 'pending' ? 'بانتظار التحقق' : 'يحتاج انتباه'; }

function AccountsPage({ accounts, setAccounts }: { accounts: Account[]; setAccounts: (a: Account[]) => void }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | AccountStatus>('all');
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const filtered = useMemo(() => accounts.filter(a => (a.email.toLowerCase().includes(query.toLowerCase()) || a.status.includes(query.toLowerCase())) && (filter === 'all' || a.status === filter)), [accounts, query, filter]);
  return <div className="min-h-full px-4 py-7 sm:px-8 sm:py-10 xl:px-12"><div className="mx-auto max-w-6xl">
    <PageHead eyebrow="ACCOUNTS / المساحة" title="الحسابات" description="راجع بيانات الدخول المخزنة، وعدّلها أو تحقق منها عندما تحتاج." action={<Link href="/accounts/new" data-testid="link-add-account" className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground shadow-[0_9px_22px_hsl(var(--primary)/.18)] transition-transform hover:-translate-y-0.5"><Plus size={17} /> حساب جديد</Link>} />
    <div className="mb-5 flex flex-col gap-3 sm:flex-row"><div className="relative flex-1"><Search size={17} className="absolute right-3.5 top-1/2 -translate-y-1/2 text-muted-foreground" /><input data-testid="input-search-accounts" value={query} onChange={e => setQuery(e.target.value)} placeholder="ابحث بالبريد أو الحالة..." className="h-12 w-full rounded-xl border border-border bg-card pr-11 pl-4 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/10" /></div><div className="flex items-center gap-1 rounded-xl border border-border bg-card p-1"><SlidersHorizontal size={16} className="mx-2 text-muted-foreground" />{([['all', 'الكل'], ['verified', 'موثق'], ['pending', 'بانتظار'], ['attention', 'مراجعة']] as const).map(([value, label]) => <button key={value} data-testid={`button-filter-${value}`} onClick={() => setFilter(value)} className={`rounded-lg px-3 py-2 text-xs transition-colors ${filter === value ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-secondary'}`}>{label}</button>)}</div></div>
    <div className="overflow-hidden rounded-2xl border border-border bg-card"><div className="hidden grid-cols-[1.5fr_.8fr_.8fr_auto] gap-4 border-b border-border bg-secondary/35 px-5 py-3 text-[11px] font-semibold text-muted-foreground sm:grid"><span>الحساب</span><span>الحالة</span><span>آخر تحقق</span><span /></div>
      {filtered.map(account => <AccountListItem key={account.id} account={account} onDelete={() => setDeleteId(account.id)} />)}
      {filtered.length === 0 && <EmptyState search={Boolean(query || filter !== 'all')} />}
    </div>
    <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground"><span>{filtered.length} من {accounts.length} حسابات</span><span className="flex items-center gap-1.5"><LockKeyhole size={13} /> الأسرار مخفية افتراضياً</span></div>
    {deleteId && <DeleteDialog account={accounts.find(a => a.id === deleteId)!} onCancel={() => setDeleteId(null)} onConfirm={() => { setAccounts(accounts.filter(a => a.id !== deleteId)); setDeleteId(null); }} />}
  </div></div>;
}

function AccountListItem({ account, onDelete }: { account: Account; onDelete: () => void }) {
  const [menu, setMenu] = useState(false);
  return <div className="group relative grid gap-3 border-b border-border px-4 py-4 last:border-b-0 sm:grid-cols-[1.5fr_.8fr_.8fr_auto] sm:items-center sm:gap-4 sm:px-5">
    <Link href={`/accounts/${account.id}`} data-testid={`link-account-row-${account.id}`} className="flex min-w-0 items-center gap-3"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-secondary font-mono-app text-sm text-primary">{account.email.slice(0, 2).toUpperCase()}</div><div className="min-w-0"><div className="truncate text-sm font-medium">{account.email}</div><div className="mt-1 text-[11px] text-muted-foreground">أضيف في {formatDate(account.createdAt)}</div></div></Link>
    <div><span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] ${account.status === 'verified' ? 'bg-primary/10 text-primary' : account.status === 'pending' ? 'bg-accent/12 text-accent' : 'bg-destructive/10 text-destructive'}`}><span className="h-1.5 w-1.5 rounded-full bg-current" />{statusLabel(account.status)}</span></div>
    <div className="text-xs text-muted-foreground sm:block">{account.lastVerifiedAt ? formatDate(account.lastVerifiedAt) : 'لم يتم بعد'}</div>
    <div className="absolute left-4 top-4 sm:static"><button data-testid={`button-account-menu-${account.id}`} onClick={() => setMenu(!menu)} className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary"><span className="mb-1 text-lg leading-none">···</span></button>{menu && <div className="absolute left-0 top-9 z-10 w-32 rounded-xl border border-border bg-card p-1.5 shadow-xl"><Link href={`/accounts/${account.id}`} data-testid={`link-edit-account-${account.id}`} className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs hover:bg-secondary"><Pencil size={13} /> تعديل</Link><button data-testid={`button-delete-account-${account.id}`} onClick={onDelete} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-xs text-destructive hover:bg-destructive/10"><Trash2 size={13} /> حذف</button></div>}</div>
  </div>;
}

function EmptyState({ search = false, compact = false }: { search?: boolean; compact?: boolean }) {
  return <div className={`flex flex-col items-center justify-center text-center ${compact ? 'py-8' : 'min-h-[280px] p-8'}`}><div className="mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-secondary text-primary"><Server size={23} /></div><h3 className="font-arabic font-semibold">{search ? 'لا توجد نتائج' : 'لا توجد حسابات بعد'}</h3><p className="mt-2 max-w-xs text-xs leading-6 text-muted-foreground">{search ? 'جرّب تغيير كلمات البحث أو الفلتر.' : 'أضف أول حساب لتبدأ إدارة مساحة البوت.'}</p>{!search && <Link href="/accounts/new" data-testid="link-empty-add-account" className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground"><Plus size={14} /> إضافة حساب</Link>}</div>;
}

function DeleteDialog({ account, onCancel, onConfirm }: { account: Account; onCancel: () => void; onConfirm: () => void }) {
  return <div className="fixed inset-0 z-50 grid place-items-center bg-foreground/25 p-4 backdrop-blur-sm"><div role="dialog" aria-modal="true" className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl"><div className="mb-5 flex items-start justify-between"><div className="grid h-11 w-11 place-items-center rounded-xl bg-destructive/10 text-destructive"><Trash2 size={20} /></div><button data-testid="button-close-delete-dialog" onClick={onCancel} className="text-muted-foreground hover:text-foreground"><X size={19} /></button></div><h2 className="font-arabic text-lg font-semibold">حذف هذا الحساب؟</h2><p className="mt-2 text-sm leading-7 text-muted-foreground">سيتم حذف <span className="font-medium text-foreground">{account.email}</span> وكل البيانات المخزنة محلياً. لا يمكن التراجع عن هذه الخطوة.</p><div className="mt-6 flex gap-2"><button data-testid="button-confirm-delete" onClick={onConfirm} className="flex-1 rounded-xl bg-destructive px-4 py-3 text-sm font-semibold text-destructive-foreground">نعم، حذف الحساب</button><button data-testid="button-cancel-delete" onClick={onCancel} className="rounded-xl border border-border px-5 py-3 text-sm font-medium hover:bg-secondary">إلغاء</button></div></div></div>;
}

function AccountForm({ mode, accounts, setAccounts }: { mode: 'new' | 'edit'; accounts: Account[]; setAccounts: (a: Account[]) => void }) {
  const params = useParams<{ id: string }>();
  const [, setLocation] = useLocation();
  const existing = accounts.find(a => a.id === params.id);
  const [form, setForm] = useState({ email: existing?.email ?? '', password: existing?.password ?? '', totpSecret: existing?.totpSecret ?? '', appPassword: existing?.appPassword ?? '', recoveryCodes: existing?.recoveryCodes ?? '' });
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState(false);
  const [verifyState, setVerifyState] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const isNew = mode === 'new' || !params.id;
  useEffect(() => { if (existing && !isNew) setForm({ email: existing.email, password: existing.password, totpSecret: existing.totpSecret, appPassword: existing.appPassword, recoveryCodes: existing.recoveryCodes }); }, [existing?.id]);
  if (!isNew && !existing) return <div className="p-8"><EmptyState /></div>;
  const update = (key: keyof typeof form, value: string) => setForm(prev => ({ ...prev, [key]: value }));
  const submit = (e: React.FormEvent) => { e.preventDefault(); if (!form.email.trim()) return; const now = new Date().toISOString(); if (isNew) { setAccounts([...accounts, { ...form, id: `acc-${Date.now()}`, status: 'pending', lastVerifiedAt: null, createdAt: now }]); } else setAccounts(accounts.map(a => a.id === existing!.id ? { ...a, ...form, status: a.status === 'verified' ? 'attention' : a.status } : a)); setSaved(true); setTimeout(() => setLocation('/accounts'), 650); };
  const verify = () => { setVerifyState('loading'); setTimeout(() => { setVerifyState('success'); if (existing) setAccounts(accounts.map(a => a.id === existing.id ? { ...a, status: 'verified', lastVerifiedAt: new Date().toISOString() } : a)); }, 850); };
  return <div className="min-h-full px-4 py-7 sm:px-8 sm:py-10 xl:px-12"><div className="mx-auto max-w-4xl">
    <Link href="/accounts" data-testid="link-back-accounts" className="mb-7 inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground"><ArrowRight size={15} /> العودة إلى الحسابات</Link>
    <PageHead eyebrow={isNew ? 'NEW ACCOUNT / حساب جديد' : 'ACCOUNT / تفاصيل الحساب'} title={isNew ? 'إضافة حساب جديد' : 'تعديل بيانات الحساب'} description={isNew ? 'أدخل بيانات الوصول الأساسية. ستبقى مخفية ولن تظهر إلا عند طلبك.' : 'حدّث معلومات الحساب بأمان. تغيير بيانات الدخول يضع الحساب للمراجعة.'} />
    <form onSubmit={submit} className="space-y-5">
      <section className="rounded-2xl border border-border bg-card p-5 sm:p-7"><div className="mb-6 flex items-start gap-3"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Mail size={18} /></div><div><h2 className="font-arabic font-semibold">بيانات البريد</h2><p className="mt-1 text-xs text-muted-foreground">البريد المستخدم للوصول إلى حساب البوت.</p></div></div><div className="grid gap-5 sm:grid-cols-2"><Field label="البريد الإلكتروني" required value={form.email} onChange={v => update('email', v)} placeholder="owner@example.com" type="email" testId="input-email" /><SecretField label="كلمة المرور" value={form.password} onChange={v => update('password', v)} revealed={Boolean(revealed.password)} onReveal={() => setRevealed(v => ({ ...v, password: !v.password }))} testId="input-password" /></div></section>
      <section className="rounded-2xl border border-border bg-card p-5 sm:p-7"><div className="mb-6 flex items-start gap-3"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent/12 text-accent"><KeyRound size={18} /></div><div><h2 className="font-arabic font-semibold">مفاتيح الوصول</h2><p className="mt-1 text-xs text-muted-foreground">تستخدم عند تفعيل الحماية والتحقق من البريد.</p></div></div><div className="grid gap-5 sm:grid-cols-2"><SecretField label="مفتاح TOTP / Secret Key" value={form.totpSecret} onChange={v => update('totpSecret', v)} revealed={Boolean(revealed.totpSecret)} onReveal={() => setRevealed(v => ({ ...v, totpSecret: !v.totpSecret }))} testId="input-totp" /><SecretField label="كلمة مرور التطبيق" value={form.appPassword} onChange={v => update('appPassword', v)} revealed={Boolean(revealed.appPassword)} onReveal={() => setRevealed(v => ({ ...v, appPassword: !v.appPassword }))} testId="input-app-password" /><div className="sm:col-span-2"><SecretField label="أكواد الاسترداد" hint="افصل بين الأكواد بمسافة" value={form.recoveryCodes} onChange={v => update('recoveryCodes', v)} revealed={Boolean(revealed.recoveryCodes)} onReveal={() => setRevealed(v => ({ ...v, recoveryCodes: !v.recoveryCodes }))} testId="input-recovery-codes" /></div></div></section>
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between"><Link href="/accounts" data-testid="link-cancel-form" className="rounded-xl px-4 py-3 text-center text-sm text-muted-foreground hover:bg-secondary">إلغاء</Link><div className="flex flex-col gap-3 sm:flex-row"><button type="button" data-testid="button-verify-account" disabled={!existing || verifyState === 'loading'} onClick={verify} className="inline-flex items-center justify-center gap-2 rounded-xl border border-primary/30 bg-primary/5 px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-50">{verifyState === 'loading' ? <RefreshCcw size={16} className="animate-spin" /> : verifyState === 'success' ? <Check size={16} /> : <ShieldCheck size={16} />}{verifyState === 'success' ? 'تم التحقق' : 'تحقق من الوصول'}</button><button type="submit" data-testid="button-save-account" className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground shadow-[0_9px_22px_hsl(var(--primary)/.18)]">{saved ? <Check size={16} /> : null}{saved ? 'تم الحفظ' : isNew ? 'حفظ الحساب' : 'حفظ التغييرات'} <ArrowLeft size={15} /></button></div></div>
      {verifyState === 'success' && <div data-testid="status-verification-success" className="flex items-center gap-2 rounded-xl border border-primary/20 bg-primary/5 px-4 py-3 text-xs text-primary"><CheckCircle2 size={16} /> تم تسجيل التحقق بنجاح. الحساب جاهز للاستخدام.</div>}
    </form>
  </div></div>;
}

function Field({ label, required, value, onChange, placeholder, type = 'text', testId }: { label: string; required?: boolean; value: string; onChange: (v: string) => void; placeholder?: string; type?: string; testId: string }) {
  return <label className="block"><span className="mb-2 block text-xs font-semibold">{label} {required && <span className="text-accent">*</span>}</span><input data-testid={testId} required={required} type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} className="h-12 w-full rounded-xl border border-input bg-background px-3.5 text-sm outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-primary focus:ring-2 focus:ring-primary/10" /></label>;
}

function SecretField({ label, hint, value, onChange, revealed, onReveal, testId }: { label: string; hint?: string; value: string; onChange: (v: string) => void; revealed: boolean; onReveal: () => void; testId: string }) {
  return <label className="block"><span className="mb-2 flex items-center justify-between text-xs font-semibold"><span>{label}</span>{hint && <span className="font-normal text-muted-foreground">{hint}</span>}</span><div className="relative"><input data-testid={testId} type={revealed ? 'text' : 'password'} value={value} onChange={e => onChange(e.target.value)} placeholder="••••••••••••" className="h-12 w-full rounded-xl border border-input bg-background px-3.5 pl-11 text-sm outline-none transition-colors placeholder:text-muted-foreground/50 focus:border-primary focus:ring-2 focus:ring-primary/10" /><button type="button" data-testid={`button-reveal-${testId}`} onClick={onReveal} className="absolute left-1.5 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground">{revealed ? <EyeOff size={16} /> : <Eye size={16} />}</button></div><span className="mt-1.5 flex items-center gap-1 text-[10px] text-muted-foreground"><LockKeyhole size={11} /> مخفي افتراضياً</span></label>;
}

function Guide() {
  const [active, setActive] = useState(1);
  const steps = [
    { number: '01', title: 'ابدأ بإضافة الحساب', text: 'من صفحة الحسابات، اضغط «حساب جديد» وابدأ بكتابة البريد الذي يصل إليه البوت.', icon: Plus },
    { number: '02', title: 'أكمل بيانات الوصول', text: 'أدخل كلمة المرور ومفتاح TOTP وكلمة مرور التطبيق. لا تظهر الأسرار بشكل مكشوف.', icon: FileKey2 },
    { number: '03', title: 'تحقق من البريد', text: 'بعد حفظ الحساب، استخدم زر «تحقق من الوصول». نتحقق من وجود البيانات ونحفظ نتيجة العملية.', icon: Mail },
    { number: '04', title: 'أبقِ الحسابات مرتبة', text: 'الحساب الموثق يظهر باللون الفيروزي. أي تغيير في مفاتيح الوصول يعيد الحساب إلى المراجعة.', icon: ClipboardCheck },
  ];
  return <div className="surface-grid min-h-full px-4 py-7 sm:px-8 sm:py-10 xl:px-12"><div className="mx-auto max-w-5xl"><PageHead eyebrow="GUIDE / كيف يعمل" title="دليل هادئ، خطوة بخطوة" description="كل ما تحتاجه لإضافة حسابات البوت والتحقق من وصول البريد، بدون مصطلحات زائدة." action={<Link href="/accounts/new" data-testid="link-guide-add-account" className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground"><Plus size={17} /> ابدأ الآن</Link>} />
    <div className="grid gap-5 lg:grid-cols-[.75fr_1.25fr]"><aside className="rounded-2xl border border-border bg-card p-3 lg:p-4">{steps.map((step, i) => { const Icon = step.icon; return <button key={step.number} data-testid={`button-guide-step-${i + 1}`} onClick={() => setActive(i + 1)} className={`flex w-full items-center gap-3 rounded-xl p-3 text-right transition-colors ${active === i + 1 ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-secondary'}`}><span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg font-mono-app text-[11px] ${active === i + 1 ? 'bg-primary-foreground/15' : 'bg-secondary text-primary'}`}>{step.number}</span><span className="flex-1 text-xs font-medium">{step.title}</span><ChevronLeft size={15} /></button>; })}<div className="mt-4 hidden rounded-xl bg-secondary/60 p-4 lg:block"><div className="flex items-center gap-2 text-xs font-semibold"><CircleHelp size={15} className="text-primary" /> ملاحظة المالك</div><p className="mt-2 text-[11px] leading-6 text-muted-foreground">هذه مساحة خاصة بك. لا تشارك بيانات الدخول أو أكواد الاسترداد مع أي شخص.</p></div></aside>
      <section className="rounded-2xl border border-border bg-card p-6 sm:p-9"><div className="mb-9 flex items-start justify-between"><div><span className="font-mono-app text-xs text-accent">STEP {String(active).padStart(2, '0')}</span><h2 className="mt-3 font-arabic text-2xl font-semibold">{steps[active - 1].title}</h2></div><div className="grid h-12 w-12 place-items-center rounded-2xl bg-accent/12 text-accent"><span className="text-xl">{active === 1 ? '+' : active === 2 ? '⌁' : active === 3 ? '@' : '✓'}</span></div></div><p className="max-w-xl text-[15px] leading-9 text-muted-foreground">{steps[active - 1].text}</p><div className="my-9 h-px bg-border" /><div className="space-y-4 text-sm leading-8">{active === 1 && <><GuideLine n="1" text="افتح صفحة الحسابات من القائمة الجانبية." /><GuideLine n="2" text="اضغط على «حساب جديد» لفتح نموذج الإضافة." /><GuideLine n="3" text="احفظ الحساب، ثم انتقل إلى خطوة التحقق." /></>}{active === 2 && <><GuideLine n="1" text="كلمة المرور تخص البريد، أما كلمة مرور التطبيق فتُنشأ من إعدادات البريد." /><GuideLine n="2" text="مفتاح TOTP هو المفتاح النصي الذي يرافق رمز المصادقة الثنائية." /><GuideLine n="3" text="يمكنك إظهار أي قيمة لحظياً بزر العين، وتبقى مخفية في الوضع الافتراضي." /></>}{active === 3 && <><GuideLine n="1" text="افتح تفاصيل الحساب المحفوظ." /><GuideLine n="2" text="اضغط «تحقق من الوصول» وانتظر ظهور رسالة النجاح." /><GuideLine n="3" text="الحساب الموثق يظهر بنقطة فيروزية وحالة «موثق»." /></>}{active === 4 && <><GuideLine n="1" text="راجع الحسابات ذات حالة «يحتاج انتباه» أولاً." /><GuideLine n="2" text="التعديلات تحفظ تلقائياً على هذا الجهاز بعد الضغط على حفظ." /><GuideLine n="3" text="احذف الحسابات القديمة من قائمة الإجراءات عند الحاجة." /></>}</div><div className="mt-10 flex justify-between border-t border-border pt-5"><button data-testid="button-guide-prev" disabled={active === 1} onClick={() => setActive(Math.max(1, active - 1))} className="inline-flex items-center gap-2 rounded-lg px-2 text-xs text-muted-foreground disabled:opacity-30"><ArrowRight size={15} /> السابق</button><button data-testid="button-guide-next" disabled={active === steps.length} onClick={() => setActive(Math.min(steps.length, active + 1))} className="inline-flex items-center gap-2 rounded-lg px-2 text-xs font-semibold text-primary disabled:opacity-30">التالي <ArrowLeft size={15} /></button></div></section></div>
  </div></div>;
}

function GuideLine({ n, text }: { n: string; text: string }) { return <div className="flex items-start gap-3"><span className="mt-1 grid h-6 w-6 shrink-0 place-items-center rounded-md bg-secondary font-mono-app text-[10px] text-primary">{n}</span><span>{text}</span></div>; }
function formatDate(value: string) { try { return new Intl.DateTimeFormat('ar', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(value)); } catch { return '—'; } }
function NotFound() { return <div className="grid min-h-[70vh] place-items-center p-8 text-center"><div><div className="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-secondary text-primary"><CircleHelp /></div><h1 className="font-arabic text-xl font-semibold">الصفحة غير موجودة</h1><Link href="/" data-testid="link-not-found-home" className="mt-4 inline-block text-sm text-primary hover:underline">العودة للرئيسية</Link></div></div>; }

export default App;