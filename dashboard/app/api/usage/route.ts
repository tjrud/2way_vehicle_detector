import { NextResponse } from "next/server";
import { getChatGPTUser } from "../../chatgpt-auth";
import { getAdminUsageData, recordUsage } from "../../usage-store";

// 로그인한 관리자에게 사용 이력을 제공하고 필요하면 중앙 사용 API로 전달한다.

export async function GET() {
  const user = await getChatGPTUser();
  if (!user || user.role !== "admin") return NextResponse.json({ error: "forbidden" }, { status: 403 });
  if (process.env.USAGE_API_ORIGIN && process.env.USAGE_API_TOKEN) {
    const response = await fetch(`${process.env.USAGE_API_ORIGIN.replace(/\/$/, "")}/api/usage/internal`, { headers: { Authorization: `Bearer ${process.env.USAGE_API_TOKEN}` }, cache: "no-store" });
    if (response.ok) return NextResponse.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  }
  return NextResponse.json(await getAdminUsageData(), { headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: Request) {
  const user = await getChatGPTUser();
  if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const body = await request.json().catch(() => ({})) as { eventType?: string; detail?: string; provider?: string };
  const allowedEvents = new Set(["page_view", "login", "signup"]);
  const eventType = allowedEvents.has(body.eventType ?? "") ? body.eventType! : "page_view";
  const identity = {
    userId: user.userId,
    displayName: user.displayName,
    email: user.email,
    role: user.role,
    provider: user.userId.startsWith("vercel:") || user.userId.startsWith("admin:") ? "password" : "chatgpt",
  } as const;
  const detail = String(body.detail ?? "대시보드").slice(0, 160);
  if (process.env.USAGE_API_ORIGIN && process.env.USAGE_API_TOKEN) {
    await fetch(`${process.env.USAGE_API_ORIGIN.replace(/\/$/, "")}/api/usage/internal`, { method: "POST", headers: { Authorization: `Bearer ${process.env.USAGE_API_TOKEN}`, "Content-Type": "application/json" }, body: JSON.stringify({ identity, eventType, detail }) }).catch(() => undefined);
  } else {
    await recordUsage(identity, eventType, detail);
  }
  return NextResponse.json({ ok: true });
}
