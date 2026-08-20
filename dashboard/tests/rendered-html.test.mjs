import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
// 서버 렌더링 결과에 필수 UI와 접근성 텍스트가 포함되는지 검증한다.
import test from "node:test";

const root = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("교차로 차량 카운터 페이지를 제공한다", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
});

test("엑셀 자동 입력 기능과 원본 보존 안내를 포함한다", async () => {
  const [page, packageJson] = await Promise.all([
    readFile(new URL("app/DashboardClient.tsx", root), "utf8"),
    readFile(new URL("package.json", root), "utf8"),
  ]);
  assert.match(page, /엑셀 자동 입력/);
  assert.doesNotMatch(page, /동연사거리 엑셀 자동 입력/);
  assert.match(page, /원본 서식과 다른 값은 그대로 유지/);
  assert.match(page, /const excelColumns = \["H", "P", "X"\]/);
  assert.match(page, /const savedSlots = Object\.entries\(records\)/);
  assert.match(page, /savedSlots\.forEach/);
  assert.match(page, /fullCalcOnLoad/);
  assert.match(page, /zip\.remove\("xl\/calcChain\.xml"\)/);
  assert.match(page, /endsWith\("\/calcChain"\)/);
  assert.match(page, /calcCompleted/);
  assert.match(packageJson, /"jszip"/);
});

test("자정 전후의 기존 날짜별 기록을 한 기록표로 복구한다", async () => {
  const [page, storage] = await Promise.all([
    readFile(new URL("app/DashboardClient.tsx", root), "utf8"),
    readFile(new URL("app/record-storage.mjs", root), "utf8"),
  ]);
  assert.match(page, /intersection-timed-records-v2/);
  assert.match(page, /intersection-timed-records-v3/);
  assert.match(page, /localStorage\.getItem\("intersection-timed-records-v1"\)/);
  assert.match(storage, /isCounts\(latestDay\["00"\]\)/);
  assert.match(page, /slot:\s*pad\(\(Number\(slot\) \+ 1\) % 96\)/);
  assert.doesNotMatch(page, /setDate|records\[date\]/);
});

test("모드별 다중 기록 슬롯 선택과 생성 기능을 제공한다", async () => {
  const page = await readFile(new URL("app/DashboardClient.tsx", root), "utf8");
  assert.match(page, /기록 슬롯/);
  assert.match(page, /\+ 새 기록/);
  assert.match(page, /이름 변경/);
  assert.match(page, /saveRecordName/);
  assert.match(page, /기록 삭제/);
  assert.match(page, /deleteRecordSet/);
  assert.match(page, /복구할 수 없습니다/);
  assert.match(page, /library\[mode\]/);
  assert.match(page, /activeRecordIds\[mode\]/);
  assert.match(page, /safeRecordName/);
  assert.match(page, /모드 3 · 12개/);
  assert.match(page, /boxMode/);
  assert.match(page, /onContextMenu/);
  assert.match(page, /movement-\$\{movement\}/);
});

test("모드 3 색상과 모드 2의 큰 조작 버튼 스타일을 제공한다", async () => {
  const css = await readFile(new URL("app/globals.css", root), "utf8");
  assert.match(css, /--left-turn:#1f6b45/);
  assert.match(css, /--straight:#69ace0/);
  assert.match(css, /--right-turn:#1f6b45/);
  assert.match(css, /\.photo-layout \.controls button \{ width:72px; height:72px;/);
});

test("모드별 번호마다 버튼 소리를 설정하고 저장한다", async () => {
  const [page, css] = await Promise.all([
    readFile(new URL("app/DashboardClient.tsx", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);
  assert.match(page, /type CounterSounds = Record<Mode/);
  assert.match(page, /counterSounds\[mode\]\[id\]/);
  assert.match(page, /번호별 소리/);
  assert.match(page, /counterSounds, volume/);
  assert.match(css, /\.counter-sound-grid/);
});

test("커스텀 카운터의 카드 클릭 및 버튼 조작 방식을 선택한다", async () => {
  const [page, css] = await Promise.all([
    readFile(new URL("app/DashboardClient.tsx", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);
  assert.match(page, /type InputStyle = "card" \| "buttons"/);
  assert.match(page, /const usesCardControls = inputStyle === "card"/);
  assert.match(page, /카운터 조작 방식/);
  assert.match(page, /교차로의 모든 번호에 동일하게 적용/);
  assert.match(page, /inputStyle, selectedVehicle, soundOn/);
  assert.match(css, /\.click-counter/);
  assert.match(css, /\.input-style-options/);
});

test("규호 모드에서 12방향과 6개 차량 분류를 집계한다", async () => {
  const [page, storage, css] = await Promise.all([
    readFile(new URL("app/DashboardClient.tsx", root), "utf8"),
    readFile(new URL("app/record-storage.mjs", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);
  assert.match(page, /const gyuhoMode/);
  assert.match(page, /\{ id: 6, area: "n1" \}.*\{ id: 4, area: "n3" \}/s);
  assert.match(page, /\{ id: 10, area: "s1" \}.*\{ id: 12, area: "s3" \}/s);
  assert.match(page, /승용/);
  assert.match(page, /버스/);
  assert.match(page, /트레일러/);
  assert.match(page, /vehicleCountKey\(id, selectedVehicle\)/);
  assert.match(page, /표 복사 또는 CSV 다운로드/);
  assert.match(storage, /"gyuho"/);
  assert.match(css, /\.vehicle-selector/);
});

test("커스텀 모드에서 교차로별 번호·방향·차선 수를 설정한다", async () => {
  const [page, storage, css] = await Promise.all([
    readFile(new URL("app/DashboardClient.tsx", root), "utf8"),
    readFile(new URL("app/record-storage.mjs", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);
  assert.match(page, /커스텀 모드/);
  assert.match(page, /커스텀 교차로 구성/);
  assert.match(page, /차선 수/);
  assert.match(page, /좌회전/);
  assert.match(page, /직진/);
  assert.match(page, /우회전/);
  assert.match(page, /저장 기록마다 다른 배치/);
  assert.match(page, /const mode: Mode = "custom"/);
  assert.doesNotMatch(page, /<nav className="mode-switch"/);
  assert.match(storage, /"custom"/);
  assert.match(css, /\.custom-layout/);
  assert.match(css, /\.custom-config-modal/);
});

test("카운터 조작마다 클릭 로그를 저장하고 내보낸다", async () => {
  const [page, storage, css] = await Promise.all([
    readFile(new URL("app/DashboardClient.tsx", root), "utf8"),
    readFile(new URL("app/record-storage.mjs", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);
  assert.match(page, /clickLogs: \[\.\.\.\(recordSet\.clickLogs/);
  assert.match(page, /클릭 시각/);
  assert.match(page, /로그 CSV/);
  assert.match(page, /최근 클릭 로그/);
  assert.match(page, /전체 로그/);
  assert.match(storage, /clickLogs: \[\]/);
  assert.match(storage, /normalizeClickLogs/);
  assert.match(css, /\.click-log-panel/);
  assert.match(css, /\.main-click-log/);
});

test("로그인, 영상 전처리, 차량 카운팅을 하나의 대시보드로 제공한다", async () => {
  const [entry, loginVisual, dashboard, preprocess, liveCounting, adminWorkspace, usageStore, usageApi, usageInternalApi, auth, passwordAuth, css] = await Promise.all([
    readFile(new URL("app/page.tsx", root), "utf8"),
    readFile(new URL("app/LoginVisual.tsx", root), "utf8"),
    readFile(new URL("app/DashboardClient.tsx", root), "utf8"),
    readFile(new URL("app/PreprocessWorkspace.tsx", root), "utf8"),
    readFile(new URL("app/LiveCountingWorkspace.tsx", root), "utf8"),
    readFile(new URL("app/AdminWorkspace.tsx", root), "utf8"),
    readFile(new URL("app/usage-store.ts", root), "utf8"),
    readFile(new URL("app/api/usage/route.ts", root), "utf8"),
    readFile(new URL("app/api/usage/internal/route.ts", root), "utf8"),
    readFile(new URL("app/chatgpt-auth.ts", root), "utf8"),
    readFile(new URL("app/password-auth.ts", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);
  assert.match(entry, /ChatGPT로 로그인/);
  assert.match(entry, /api\/auth\/login/);
  assert.match(entry, /api\/auth\/signup/);
  assert.match(entry, /api\/auth\/reset/);
  assert.match(entry, /비밀번호를 잊으셨나요/);
  assert.doesNotMatch(loginVisual, /intersection-dashboard-hero\.png/);
  assert.match(loginVisual, /hologram-projection/);
  assert.match(loginVisual, /future-features/);
  assert.match(entry, /<b>DASHBOARD<\/b>/);
  assert.match(entry, /<LoginVisual \/>/);
  assert.match(entry, /getChatGPTUser/);
  assert.match(auth, /signin-with-chatgpt/);
  assert.match(passwordAuth, /scryptSync/);
  assert.match(passwordAuth, /DASHBOARD_ACCOUNT_COOKIE/);
  assert.match(passwordAuth, /ADMIN_ID/);
  assert.match(passwordAuth, /ADMIN_PASSWORD/);
  assert.match(entry, /이메일 또는 관리자 아이디/);
  assert.match(dashboard, /영상 전처리/);
  assert.match(dashboard, /차량 카운팅/);
  assert.match(dashboard, /현황 · 빠른 실행/);
  assert.match(dashboard, /NAVIGATION/);
  assert.match(dashboard, /PROJECTS/);
  assert.match(dashboard, /nav-group/);
  assert.match(dashboard, /누적 통행량/);
  assert.match(dashboard, /차량 카운팅 이어하기/);
  assert.match(dashboard, /sidebar-account-copy/);
  assert.match(dashboard, /계정 설정 열기/);
  assert.match(dashboard, /계정 전환/);
  assert.match(dashboard, /sidebarCollapsed/);
  assert.match(dashboard, /사이드바 접기/);
  assert.match(dashboard, /사이드바 펼치기/);
  assert.match(dashboard, /현재 로그인 계정/);
  assert.match(dashboard, /signout-with-chatgpt/);
  assert.doesNotMatch(dashboard, /return_to=%2Fsignin-with-chatgpt/);
  assert.match(dashboard, /api\/auth\/logout/);
  assert.match(preprocess, /webkitdirectory/);
  assert.match(preprocess, /전처리 실행 파일 만들기/);
  assert.match(preprocess, /PowerShell · FFmpeg/);
  assert.match(dashboard, /workspaceView === "live"/);
  assert.match(dashboard, /workspaceView === "admin"/);
  assert.match(dashboard, /user\.role === "admin"/);
  assert.match(dashboard, /관리자 페이지/);
  assert.match(adminWorkspace, /MEMBER ADMINISTRATION/);
  assert.match(adminWorkspace, /가입 회원 목록/);
  assert.match(adminWorkspace, /사용 기록/);
  assert.match(adminWorkspace, /\/api\/usage/);
  assert.match(usageStore, /INSERT INTO members/);
  assert.match(usageStore, /INSERT INTO usage_events/);
  assert.match(usageApi, /user\.role !== "admin"/);
  assert.match(usageApi, /USAGE_API_ORIGIN/);
  assert.match(usageInternalApi, /Bearer/);
  assert.match(dashboard, /eventType: "page_view"/);
  assert.match(dashboard, /실시간 영상 계수/);
  assert.match(liveCounting, /navigator\.mediaDevices\.getUserMedia/);
  assert.match(liveCounting, /영상 파일 열기/);
  assert.match(liveCounting, /재생속도/);
  assert.match(liveCounting, /props\.onCount/);
  assert.match(liveCounting, /props\.onSaveNext/);
  assert.match(css, /\.dashboard-app/);
  assert.match(css, /\.preprocess-workspace/);
  assert.match(css, /\.live-count-workspace/);
  assert.match(css, /\.admin-workspace/);
  assert.match(css, /\.login-page/);
  assert.match(css, /\.intersection-art/);
  assert.match(css, /@keyframes holo-spin/);
  assert.match(css, /\.hologram-projection/);
  assert.match(css, /\.digital-intersection/);
  assert.match(css, /--font-korean/);
  assert.doesNotMatch(css, /font-display/);
  assert.match(css, /\.dashboard-settings/);
  assert.match(css, /\.sidebar-collapsed/);
  assert.match(css, /\.sidebar-collapse/);
  assert.match(css, /\.account-menu/);
  assert.match(css, /\.dashboard-home/);
  assert.match(css, /\.home-stats/);
});
