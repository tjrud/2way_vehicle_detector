import { NextResponse } from "next/server";
import { AUTH_COOKIE_OPTIONS, createLocalAccount, createPasswordSession, DASHBOARD_ACCOUNT_COOKIE, DASHBOARD_SESSION_COOKIE, passwordAuthConfigured } from "../../../password-auth";
import { recordUsage } from "../../../usage-store";

// 브라우저 기반 계정을 생성하고 곧바로 로그인 세션을 발급한다.

export async function POST(request: Request) {
  const data = await request.formData();
  const name = String(data.get("name") ?? "").trim();
  const email = String(data.get("email") ?? "").trim().toLowerCase();
  const password = String(data.get("password") ?? "");
  const confirm = String(data.get("confirm") ?? "");
  const target = new URL("/", request.url);
  if (!passwordAuthConfigured()) target.searchParams.set("auth_error", "config");
  else if (name.length < 2 || !email.includes("@") || password.length < 8 || password !== confirm) target.searchParams.set("auth_error", "signup");
  if (target.searchParams.has("auth_error")) { target.searchParams.set("auth_view", "signup"); return NextResponse.redirect(target, 303); }
  const local = createLocalAccount(name, email, password);
  await recordUsage({ userId: `vercel:${local.account.email}`, displayName: local.account.name, email: local.account.email, role: "user", provider: "password" }, "signup", "회원가입");
  const session = createPasswordSession(email);
  const response = NextResponse.redirect(target, 303);
  response.cookies.set(DASHBOARD_ACCOUNT_COOKIE, local.value, { ...AUTH_COOKIE_OPTIONS, maxAge: local.maxAge });
  response.cookies.set(DASHBOARD_SESSION_COOKIE, session.value, { ...AUTH_COOKIE_OPTIONS, maxAge: session.maxAge });
  return response;
}
