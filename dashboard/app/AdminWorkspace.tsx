"use client";

// 배포 모드 관리자가 회원과 최근 사용 이벤트를 조회하는 관리 화면이다.

import { useEffect, useMemo, useState } from "react";

type Member = { id: string; name: string; email: string; provider: string; role: string; createdAt: number; lastSeenAt: number; loginCount: number };
type UsageEvent = { id: number; memberId: string; memberName: string; memberEmail: string; eventType: string; detail: string; createdAt: number };
type AdminData = { available: boolean; members: Member[]; events: UsageEvent[] };
type Props = { displayName: string; adminId: string };

const dateTime = (value: number) => new Intl.DateTimeFormat("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
const eventLabel = (event: UsageEvent) => event.eventType === "signup" ? "회원가입" : event.eventType === "login" ? "로그인" : event.detail;
const providerLabel = (provider: string) => provider === "chatgpt" ? "ChatGPT" : "이메일";

export default function AdminWorkspace({ displayName, adminId }: Props) {
  const [data, setData] = useState<AdminData>({ available: true, members: [], events: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [eventFilter, setEventFilter] = useState("all");

  const loadData = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/usage", { cache: "no-store" });
      if (!response.ok) throw new Error("회원 기록을 불러오지 못했습니다.");
      setData(await response.json() as AdminData);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "회원 기록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadData(); }, []);

  const todayStart = new Date().setHours(0, 0, 0, 0);
  const activeToday = data.members.filter((member) => member.lastSeenAt >= todayStart).length;
  const totalLogins = data.members.reduce((sum, member) => sum + member.loginCount, 0);
  const visibleMembers = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? data.members.filter((member) => `${member.name} ${member.email}`.toLowerCase().includes(needle)) : data.members;
  }, [data.members, query]);
  const visibleEvents = useMemo(() => data.events.filter((event) => eventFilter === "all" || event.eventType === eventFilter), [data.events, eventFilter]);

  return (
    <section className="admin-workspace">
      <header className="admin-hero">
        <div><p>MEMBER ADMINISTRATION</p><h1>회원 및 사용 기록</h1><span>가입된 회원과 대시보드 사용 이력을 확인합니다.</span></div>
        <div className="admin-identity"><i>✓</i><div><small>{displayName} · 최고 관리자</small><b>{adminId}</b></div></div>
      </header>

      <div className="admin-member-metrics">
        <article><span>가입 회원</span><b>{data.members.length.toLocaleString()}</b><small>관리자 계정 제외</small></article>
        <article><span>오늘 활동 회원</span><b>{activeToday.toLocaleString()}</b><small>오늘 접속 또는 기능 사용</small></article>
        <article><span>누적 로그인</span><b>{totalLogins.toLocaleString()}</b><small>회원 로그인 성공 횟수</small></article>
        <article><span>사용 기록</span><b>{data.events.length.toLocaleString()}</b><small>최근 최대 1,000건</small></article>
      </div>

      {!data.available && <div className="admin-data-notice">중앙 회원 저장소가 연결되지 않은 배포 환경입니다. Sites 운영 주소에서 전체 회원 기록을 확인하세요.</div>}
      {error && <div className="admin-data-error" role="alert">{error}<button type="button" onClick={loadData}>다시 시도</button></div>}

      <section className="admin-table-panel member-list-panel">
        <header><div><b>가입 회원 목록</b><span>최근 활동 순으로 표시됩니다.</span></div><div className="admin-table-tools"><label><span>회원 검색</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="이름 또는 이메일" /></label><button type="button" onClick={loadData} disabled={loading}>{loading ? "불러오는 중" : "새로고침"}</button></div></header>
        <div className="admin-table-scroll"><table><thead><tr><th>회원</th><th>가입 방식</th><th>가입일</th><th>최근 활동</th><th>로그인</th><th>상태</th></tr></thead><tbody>{visibleMembers.map((member) => <tr key={member.id}><td><div className="member-cell"><i>{member.name.slice(0, 1).toUpperCase()}</i><span><b>{member.name}</b><small>{member.email}</small></span></div></td><td><span className={`provider-badge ${member.provider}`}>{providerLabel(member.provider)}</span></td><td>{dateTime(member.createdAt)}</td><td>{dateTime(member.lastSeenAt)}</td><td>{member.loginCount.toLocaleString()}회</td><td><span className="member-active">활성</span></td></tr>)}</tbody></table>{!loading && visibleMembers.length === 0 && <p className="admin-empty">{query ? "검색 결과가 없습니다." : "아직 가입된 회원이 없습니다."}</p>}</div>
      </section>

      <section className="admin-table-panel usage-list-panel">
        <header><div><b>사용 기록</b><span>회원가입, 로그인과 주요 화면 진입 기록입니다.</span></div><label className="usage-filter"><span>기록 종류</span><select value={eventFilter} onChange={(event) => setEventFilter(event.target.value)}><option value="all">전체 기록</option><option value="signup">회원가입</option><option value="login">로그인</option><option value="page_view">화면 사용</option></select></label></header>
        <div className="admin-table-scroll"><table><thead><tr><th>시각</th><th>회원</th><th>활동</th><th>상세</th></tr></thead><tbody>{visibleEvents.map((event) => <tr key={event.id}><td>{dateTime(event.createdAt)}</td><td><b>{event.memberName}</b><small className="event-email">{event.memberEmail}</small></td><td><span className={`event-badge ${event.eventType}`}>{eventLabel(event)}</span></td><td>{event.detail}</td></tr>)}</tbody></table>{!loading && visibleEvents.length === 0 && <p className="admin-empty">표시할 사용 기록이 없습니다.</p>}</div>
      </section>
    </section>
  );
}
