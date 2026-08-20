# 교차로 차량 카운터 대시보드

이 폴더는 `2way 교차로 차량 분석 통합 시스템`의 Next.js 대시보드 소스입니다. 전체 사용법과 영상 분석 설정은 상위 폴더의 `README.md`를 기준으로 합니다.

## 통합 모드

일반 사용자는 상위 폴더의 배치 파일을 사용합니다.

1. 최초 1회 `설치.bat` 실행
2. 이후 `통합실행.bat` 실행
3. 브라우저에서 `http://127.0.0.1:3000` 사용
4. 종료할 때 `통합종료.bat` 실행

통합 모드에서는 `LOCAL_DASHBOARD=1`이 적용되어 로컬 사용자로 바로 열립니다. 대시보드는 `http://127.0.0.1:8765`의 분석 API에 자동 연결되므로 토큰을 복사하지 않습니다.

## 대시보드 기능

- AI 영상 분석 시작, 중지 및 진행 상태 확인
- 5분 시험 분석과 전체 영상 분석
- 영상별 CSV 생성 상태 및 결과 폴더 열기
- 수동 교차로 차량 집계
- 00:00부터 15분 단위 기록 저장
- 기록 슬롯 생성, 이름 변경, 삭제 및 CSV 다운로드
- 교차로 배치별 좌회전·직진·우회전 집계
- 차량 분류형 수동 집계 모드
- 전처리 명령 생성과 라이브 카운팅 보조 화면
- 브라우저 로컬 저장소를 이용한 수동 집계 내용 유지

AI 분석은 브라우저가 직접 수행하지 않습니다. 독립 Python 프로세스가 영상을 처리하고 `analysis_status.json`, 영상별 CSV와 오류 로그를 갱신하며, 대시보드는 상태와 결과만 조회합니다.

## 개발 모드

상위 폴더의 `설치.bat`을 실행했다면 휴대용 Node.js를 사용할 수 있습니다.

```powershell
$env:PATH = "..\.runtime\node;$env:PATH"
npm install
npm run dev
```

프로덕션 빌드 확인:

```powershell
$env:LOCAL_DASHBOARD = "1"
$env:AUTH_SECRET = "local-dashboard-only"
npm run build
```

## 환경 변수

- `LOCAL_DASHBOARD=1`: 로컬 통합 모드 로그인 우회
- `AUTH_SECRET`: 세션 서명용 비밀값
- `DASHBOARD_NAME`: 배포 모드 사용자 표시 이름
- `DASHBOARD_EMAIL`: 배포 모드 로그인 이메일
- `DASHBOARD_PASSWORD`: 배포 모드 로그인 비밀번호

비밀번호와 실제 `AUTH_SECRET`은 GitHub에 커밋하지 않습니다.

## 주요 소스

- `app/page.tsx`: 로컬 통합 모드와 배포 로그인 분기
- `app/AiAnalysisWorkspace.tsx`: 로컬 분석 API 연결 및 작업 제어
- `app/DashboardClient.tsx`: 전체 화면 전환과 수동 집계 상태 관리
- `app/LiveCountingWorkspace.tsx`: 라이브 카운팅 보조 화면
- `app/PreprocessWorkspace.tsx`: 영상 전처리 명령 생성
- `app/record-storage.mjs`: 브라우저 기록 저장·복원
- `app/excel-mapping.mjs`: 엑셀 셀 매핑

## 데이터와 보안

- AI 영상은 로컬 Python 분석기에 전달되며 외부로 업로드되지 않습니다.
- 수동 집계 데이터는 현재 브라우저의 로컬 저장소에 보관됩니다.
- 다른 컴퓨터나 브라우저와 자동 동기화되지 않으므로 필요한 기록은 CSV로 내려받아 보관합니다.
- 로컬 분석 API는 허용된 로컬 대시보드 Origin 또는 명시적인 토큰 요청만 받습니다.

## 배포 모드

Vercel 등 외부 배포에서는 `LOCAL_DASHBOARD`를 설정하지 않고 로그인 환경 변수를 구성합니다. 외부에 배포된 대시보드는 사용자 컴퓨터의 `127.0.0.1:8765`에 직접 접근할 수 있어야 AI 분석 제어가 동작합니다.
