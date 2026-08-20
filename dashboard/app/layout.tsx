import type { Metadata } from "next";
import { Noto_Sans_KR } from "next/font/google";
import "./globals.css";

// 모든 페이지에 한국어 웹폰트와 공통 메타데이터를 적용하는 루트 레이아웃이다.

const notoSansKr = Noto_Sans_KR({
  variable: "--font-korean",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800", "900"],
});
export const metadata: Metadata = {
  title: "교차로 차량 조사 대시보드",
  description: "로그인 후 영상 전처리, 실시간 영상 계수, 차량 카운팅을 한 곳에서 관리합니다.",
  icons: { icon: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body className={notoSansKr.variable}>{children}</body>
    </html>
  );
}
