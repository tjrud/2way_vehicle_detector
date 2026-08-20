import { NextResponse } from "next/server";
import { getAdminUsageData, recordUsage, type UsageIdentity } from "../../../usage-store";

// 서버 간 Bearer 토큰으로만 접근 가능한 내부 사용 이력 API다.

const authorized = (request: Request) => {
  const token = process.env.USAGE_API_TOKEN;
  return Boolean(token && request.headers.get("authorization") === `Bearer ${token}`);
};

export async function GET(request: Request) {
  if (!authorized(request)) return NextResponse.json({ error: "forbidden" }, { status: 403 });
  return NextResponse.json(await getAdminUsageData(), { headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: Request) {
  if (!authorized(request)) return NextResponse.json({ error: "forbidden" }, { status: 403 });
  const body = await request.json().catch(() => null) as { identity?: UsageIdentity; eventType?: string; detail?: string } | null;
  if (!body?.identity || !body.eventType) return NextResponse.json({ error: "invalid" }, { status: 400 });
  await recordUsage(body.identity, body.eventType, String(body.detail ?? "대시보드"));
  return NextResponse.json({ ok: true });
}
