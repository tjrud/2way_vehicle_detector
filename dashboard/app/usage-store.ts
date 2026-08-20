// Cloudflare D1이 있을 때 회원·사용 이력을 저장하고, 로컬에서는 안전하게 비활성화한다.
type D1Result<T> = { results?: T[] };
type D1Statement = {
  bind: (...values: unknown[]) => D1Statement;
  run: () => Promise<unknown>;
  all: <T>() => Promise<D1Result<T>>;
};
type D1DatabaseLike = { prepare: (query: string) => D1Statement };

export type UsageIdentity = {
  userId: string;
  displayName: string;
  email: string;
  role: "admin" | "user";
  provider: "password" | "chatgpt";
};

export type MemberRow = {
  id: string;
  name: string;
  email: string;
  provider: string;
  role: string;
  createdAt: number;
  lastSeenAt: number;
  loginCount: number;
};

export type UsageRow = {
  id: number;
  memberId: string;
  memberName: string;
  memberEmail: string;
  eventType: string;
  detail: string;
  createdAt: number;
};

async function getDatabase(): Promise<D1DatabaseLike | null> {
  try {
    const runtime = await import("cloudflare:workers");
    return (runtime.env as unknown as { DB?: D1DatabaseLike }).DB ?? null;
  } catch {
    return null;
  }
}

export async function recordUsage(identity: UsageIdentity, eventType: string, detail: string) {
  const db = await getDatabase();
  if (!db) {
    if (!process.env.USAGE_API_ORIGIN || !process.env.USAGE_API_TOKEN) return false;
    const response = await fetch(`${process.env.USAGE_API_ORIGIN.replace(/\/$/, "")}/api/usage/internal`, {
      method: "POST",
      headers: { Authorization: `Bearer ${process.env.USAGE_API_TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ identity, eventType, detail }),
    }).catch(() => null);
    return Boolean(response?.ok);
  }
  const now = Date.now();
  const loginIncrement = eventType === "login" ? 1 : 0;
  await db.prepare(`INSERT INTO members (id, name, email, provider, role, created_at, last_seen_at, login_count)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET name = excluded.name, email = excluded.email, provider = excluded.provider,
      role = excluded.role, last_seen_at = excluded.last_seen_at, login_count = members.login_count + ?`)
    .bind(identity.userId, identity.displayName, identity.email, identity.provider, identity.role, now, now, loginIncrement, loginIncrement).run();
  await db.prepare("INSERT INTO usage_events (member_id, member_name, member_email, event_type, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(identity.userId, identity.displayName, identity.email, eventType, detail.slice(0, 160), now).run();
  return true;
}

export async function getAdminUsageData() {
  const db = await getDatabase();
  if (!db) return { available: false, members: [] as MemberRow[], events: [] as UsageRow[] };
  const members = await db.prepare(`SELECT id, name, email, provider, role, created_at AS createdAt,
    last_seen_at AS lastSeenAt, login_count AS loginCount FROM members WHERE role != 'admin' ORDER BY last_seen_at DESC LIMIT 500`).all<MemberRow>();
  const events = await db.prepare(`SELECT id, member_id AS memberId, member_name AS memberName, member_email AS memberEmail,
    event_type AS eventType, detail, created_at AS createdAt FROM usage_events WHERE member_id NOT LIKE 'admin:%' ORDER BY created_at DESC LIMIT 1000`).all<UsageRow>();
  return { available: true, members: members.results ?? [], events: events.results ?? [] };
}
