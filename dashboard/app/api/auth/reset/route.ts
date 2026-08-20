import { NextResponse } from "next/server";
import { AUTH_COOKIE_OPTIONS, canResetLocalAccount, createLocalAccount, createPasswordSession, DASHBOARD_ACCOUNT_COOKIE, DASHBOARD_SESSION_COOKIE } from "../../../password-auth";

// 동일 브라우저에 저장된 계정과 이메일을 확인해 비밀번호를 재설정한다.

export async function POST(request: Request) {
  const data = await request.formData();
  const email = String(data.get("email") ?? "").trim().toLowerCase();
  const password = String(data.get("password") ?? "");
  const confirm = String(data.get("confirm") ?? "");
  const target = new URL("/", request.url);
  const account = await canResetLocalAccount(email);
  if (!account || password.length < 8 || password !== confirm) {
    target.searchParams.set("auth_view", "reset"); target.searchParams.set("auth_error", "reset");
    return NextResponse.redirect(target, 303);
  }
  const updated = createLocalAccount(account.name, account.email, password);
  const session = createPasswordSession(account.email);
  const response = NextResponse.redirect(target, 303);
  response.cookies.set(DASHBOARD_ACCOUNT_COOKIE, updated.value, { ...AUTH_COOKIE_OPTIONS, maxAge: updated.maxAge });
  response.cookies.set(DASHBOARD_SESSION_COOKIE, session.value, { ...AUTH_COOKIE_OPTIONS, maxAge: session.maxAge });
  return response;
}
