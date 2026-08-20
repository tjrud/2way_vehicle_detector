import { NextResponse } from "next/server";
import { createPasswordSession, DASHBOARD_SESSION_COOKIE, getPasswordIdentity, validPasswordCredentials } from "../../../password-auth";
import { recordUsage } from "../../../usage-store";

// 외부 배포 모드의 비밀번호 로그인을 검증하고 세션 쿠키를 발급한다.

export async function POST(request: Request) {
  const data = await request.formData();
  const email = String(data.get("email") ?? "").trim();
  const password = String(data.get("password") ?? "");
  const url = new URL("/", request.url);
  if (!await validPasswordCredentials(email, password)) {
    url.searchParams.set("auth_error", "invalid");
    return NextResponse.redirect(url, 303);
  }
  const identity = await getPasswordIdentity(email);
  if (identity) await recordUsage(identity, "login", "대시보드 로그인");
  const session = createPasswordSession(email);
  const response = NextResponse.redirect(url, 303);
  response.cookies.set(DASHBOARD_SESSION_COOKIE, session.value, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: session.maxAge,
  });
  return response;
}
