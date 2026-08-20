"use client";

// 로그인 화면 우측에 조사 흐름과 시스템 기능을 시각적으로 소개한다.

import { useState } from "react";

const features = [
  { code: "01", name: "현장 조사", detail: "교차로 정보와 환경을 체계적으로 기록", metric: "READY", unit: "FIELD SURVEY" },
  { code: "02", name: "영상 전처리", detail: "촬영 영상을 분석 가능한 형식으로 변환", metric: "75%", unit: "PROCESSING" },
  { code: "03", name: "차량 카운팅", detail: "15분 단위 통행량을 빠르고 정확하게 집계", metric: "128", unit: "VEHICLES" },
  { code: "04", name: "데이터 관리", detail: "기록과 결과를 한곳에서 검토하고 내보내기", metric: "96", unit: "TIME SLOTS" },
] as const;

export default function LoginVisual() {
  const [active, setActive] = useState(0);
  const selected = features[active];

  function moveGlow(event: React.PointerEvent<HTMLElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty("--glow-x", `${event.clientX - bounds.left}px`);
    event.currentTarget.style.setProperty("--glow-y", `${event.clientY - bounds.top}px`);
  }

  return (
    <aside className="login-visual futuristic-login" onPointerMove={moveGlow}>
      <span className="login-pointer-glow" aria-hidden="true" />
      <header className="future-heading">
        <small>INTERSECTION CONTROL</small>
        <h2>교차로를 한눈에,<br />기록은 <em>빈틈없이.</em></h2>
        <p>현장 조사부터 영상 전처리, 차량 카운팅까지<br />모든 흐름을 하나의 관제 화면에서 관리하세요.</p>
      </header>

      <nav className="future-features" aria-label="대시보드 주요 기능">
        {features.map((feature, index) => (
          <button
            className={active === index ? "active" : ""}
            key={feature.code}
            type="button"
            onPointerEnter={() => setActive(index)}
            onFocus={() => setActive(index)}
            onClick={() => setActive(index)}
          >
            <span>{feature.code}</span>
            <div><b>{feature.name}</b><small>{feature.detail}</small></div>
          </button>
        ))}
      </nav>

      <section className="command-monitor" aria-label="교차로 관제 화면 미리보기">
        <header><span>IC</span><b>CONTROL CENTER</b><i /> <small>LIVE</small></header>
        <div className="monitor-content">
          <div className="monitor-stats"><article><small>15분 집계</small><b>96</b><em>TIME SLOTS</em></article><article><small>처리 작업</small><b>03</b><em>ACTIVE JOBS</em></article></div>
          <div className="monitor-chart"><header><b>시간대별 차량 수</b><small>24 HOURS</small></header><svg viewBox="0 0 320 100" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#49e6ef" stopOpacity=".38"/><stop offset="1" stopColor="#49e6ef" stopOpacity="0"/></linearGradient></defs><path d="M0 82 C25 78 38 60 58 66 S90 25 112 38 S145 90 168 62 S204 18 226 35 S250 78 274 54 S300 30 320 39 L320 100 L0 100Z" fill="url(#chart-fill)"/><path d="M0 82 C25 78 38 60 58 66 S90 25 112 38 S145 90 168 62 S204 18 226 35 S250 78 274 54 S300 30 320 39" fill="none" stroke="#54e5ef" strokeWidth="2"/></svg></div>
          <div className="monitor-events"><b>최근 작업</b><span><i /> 교차로 조사 완료 <time>14:32</time></span><span><i /> 영상 전처리 완료 <time>14:20</time></span></div>
        </div>
      </section>

      <div className="digital-intersection" aria-hidden="true"><i className="digital-road road-x"/><i className="digital-road road-y"/><span className="digital-crossing crossing-a"/><span className="digital-crossing crossing-b"/><span className="digital-crossing crossing-c"/><span className="digital-crossing crossing-d"/></div>
      <div className="hologram-projection" key={selected.code} aria-live="polite"><i/><i/><i/><span>{selected.code}</span><div><small>{selected.name}</small><b>{selected.metric}</b><em>{selected.unit}</em></div></div>
      <div className="future-coordinate coordinate-a" aria-hidden="true">37.9038° N<br/>127.0607° E</div>
      <div className="future-coordinate coordinate-b" aria-hidden="true">SYSTEM ONLINE <i/></div>
    </aside>
  );
}
