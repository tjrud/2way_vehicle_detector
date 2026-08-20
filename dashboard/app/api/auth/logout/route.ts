import { NextResponse } from "next/server";
import { DASHBOARD_SESSION_COOKIE } from "../../../password-auth";

// 현재 세션 쿠키를 즉시 만료시키고 로그인 화면으로 돌려보낸다.

export async function GET(request: Request) {
  const response = NextResponse.redirect(new URL("/", request.url));
  response.cookies.set(DASHBOARD_SESSION_COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
  return response;
}
