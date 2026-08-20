"use client";

// 재생 중인 현장 영상을 보면서 키보드/버튼으로 수동 계수하는 보조 작업공간이다.

import { useEffect, useRef, useState } from "react";

type Lane = { id: number; movement: string; approach: string };
type SlotOption = { key: string; label: string };
type RecordOption = { id: string; name: string; saved: number };

type Props = {
  lanes: Lane[];
  counts: Record<string, number>;
  total: number;
  slot: string;
  slots: SlotOption[];
  recordId: string;
  records: RecordOption[];
  savedCurrent: boolean;
  onCount: (id: number, amount: -1 | 1) => void;
  onSlotChange: (slot: string) => void;
  onRecordChange: (recordId: string) => void;
  onSaveNext: () => void;
  onOpenCounter: () => void;
};

const formatVideoTime = (seconds: number) => {
  if (!Number.isFinite(seconds)) return "00:00:00";
  const value = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remaining = value % 60;
  return [hours, minutes, remaining].map((part) => String(part).padStart(2, "0")).join(":");
};

export default function LiveCountingWorkspace(props: Props) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const videoShellRef = useRef<HTMLDivElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const objectUrlRef = useRef("");
  const [source, setSource] = useState<"none" | "file" | "camera">("none");
  const [fileName, setFileName] = useState("");
  const [cameraError, setCameraError] = useState("");
  const [speed, setSpeed] = useState(1);
  const [videoTime, setVideoTime] = useState(0);
  const [duration, setDuration] = useState(0);

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  };

  const clearObjectUrl = () => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    objectUrlRef.current = "";
  };

  useEffect(() => () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
  }, []);

  const loadVideo = (file: File | undefined) => {
    if (!file) return;
    stopCamera();
    clearObjectUrl();
    const url = URL.createObjectURL(file);
    objectUrlRef.current = url;
    setSource("file");
    setFileName(file.name);
    setCameraError("");
    setVideoTime(0);
    setDuration(0);
    requestAnimationFrame(() => {
      if (!videoRef.current) return;
      videoRef.current.srcObject = null;
      videoRef.current.src = url;
      videoRef.current.playbackRate = speed;
      void videoRef.current.play().catch(() => undefined);
    });
  };

  const startCamera = async () => {
    try {
      stopCamera();
      clearObjectUrl();
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false });
      streamRef.current = stream;
      setSource("camera");
      setFileName("실시간 카메라");
      setCameraError("");
      requestAnimationFrame(() => {
        if (!videoRef.current) return;
        videoRef.current.removeAttribute("src");
        videoRef.current.srcObject = stream;
        void videoRef.current.play();
      });
    } catch {
      setCameraError("카메라를 열 수 없습니다. 브라우저의 카메라 권한과 연결 상태를 확인해 주세요.");
    }
  };

  const changeSpeed = (nextSpeed: number) => {
    setSpeed(nextSpeed);
    if (videoRef.current && source === "file") videoRef.current.playbackRate = nextSpeed;
  };

  const requestFullscreen = () => void videoShellRef.current?.requestFullscreen?.();
  const requestPictureInPicture = () => {
    const video = videoRef.current as (HTMLVideoElement & { requestPictureInPicture?: () => Promise<PictureInPictureWindow> }) | null;
    if (video?.requestPictureInPicture) void video.requestPictureInPicture();
  };

  return (
    <section className="live-count-workspace">
      <header className="live-count-heading">
        <div><p>LIVE VIDEO COUNTING</p><h1>실시간 영상 계수</h1><span>영상을 확인하면서 차선별 차량을 바로 계수하고 현재 15분 기록에 저장합니다.</span></div>
        <div className="live-heading-total"><small>현재 구간 합계</small><b>{props.total.toLocaleString()}</b><span>대</span></div>
      </header>

      <section className="live-record-bar" aria-label="실시간 영상 계수 기록 설정">
        <label><span>기록 슬롯</span><select value={props.recordId} onChange={(event) => props.onRecordChange(event.target.value)}>{props.records.map((record) => <option value={record.id} key={record.id}>{record.name} · {record.saved}/96 저장</option>)}</select></label>
        <label><span>기록 시간</span><select value={props.slot} onChange={(event) => props.onSlotChange(event.target.value)}>{props.slots.map((item) => <option value={item.key} key={item.key}>{item.label}</option>)}</select></label>
        <span className={`live-save-state ${props.savedCurrent ? "saved" : "draft"}`}>{props.savedCurrent ? "저장된 구간" : "작성 중"}</span>
        <button type="button" className="live-open-counter" onClick={props.onOpenCounter}>교차로 설정 · 기록 보기</button>
      </section>

      <div className="live-count-grid">
        <section className="live-video-card">
          <header><div><b>영상 모니터</b><span>{source === "camera" ? "LIVE INPUT" : source === "file" ? fileName : "영상 소스를 연결하세요"}</span></div><i className={source === "none" ? "offline" : "online"}>{source === "camera" ? "LIVE" : source === "file" ? "PLAYBACK" : "OFFLINE"}</i></header>
          <div className={`live-video-shell ${source === "none" ? "empty" : ""}`} ref={videoShellRef}>
            <video ref={videoRef} controls={source === "file"} muted={source === "camera"} playsInline onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)} onTimeUpdate={(event) => setVideoTime(event.currentTarget.currentTime)} />
            {source === "none" && <div className="live-video-empty"><span>◉</span><b>영상 소스를 선택하세요</b><p>저장된 영상을 열거나 연결된 카메라의 실시간 화면을 확인할 수 있습니다.</p><div><button type="button" onClick={() => fileInputRef.current?.click()}>영상 파일 열기</button><button type="button" onClick={startCamera}>카메라 연결</button></div></div>}
            {source !== "none" && <div className="live-video-overlay"><span><i /> {source === "camera" ? "실시간 입력" : "로컬 영상"}</span><time>{source === "camera" ? "LIVE" : `${formatVideoTime(videoTime)} / ${formatVideoTime(duration)}`}</time></div>}
          </div>
          <input ref={fileInputRef} className="live-hidden-input" type="file" accept="video/*,.mp4,.mov,.m4v,.avi,.webm" onChange={(event) => loadVideo(event.target.files?.[0])} />
          <footer className="live-video-toolbar">
            <div><button type="button" onClick={() => fileInputRef.current?.click()}>영상 열기</button><button type="button" onClick={startCamera}>카메라 연결</button></div>
            {source === "file" && <div className="playback-speeds"><span>재생속도</span>{[0.5, 1, 1.5, 2, 3].map((value) => <button type="button" className={speed === value ? "active" : ""} key={value} onClick={() => changeSpeed(value)}>{value}×</button>)}</div>}
            <div className="video-view-actions"><button type="button" disabled={source === "none"} onClick={requestPictureInPicture}>PIP</button><button type="button" disabled={source === "none"} onClick={requestFullscreen}>전체화면</button></div>
          </footer>
          {cameraError && <p className="live-camera-error" role="alert">{cameraError}</p>}
        </section>

        <aside className="live-counter-card">
          <header><div><b>차선별 계수</b><span>현재 시간 · {props.slots[Number(props.slot)].label}</span></div><strong>{props.lanes.length}개 차선</strong></header>
          <div className="live-lane-list">{props.lanes.map((lane) => {
            const value = props.counts[String(lane.id)] ?? 0;
            return <article className="live-lane-counter" key={`${lane.approach}-${lane.id}`}><div className="lane-counter-name"><span>{lane.id}</span><div><b>{lane.id}번 · {lane.movement}</b><small>{lane.approach}</small></div></div><output>{value.toLocaleString()}</output><div className="lane-counter-actions"><button type="button" disabled={value === 0} onClick={() => props.onCount(lane.id, -1)} aria-label={`${lane.id}번 1대 빼기`}>−</button><button type="button" onClick={() => props.onCount(lane.id, 1)} aria-label={`${lane.id}번 1대 추가`}>＋</button></div></article>;
          })}</div>
          <footer><div><small>현재 기록</small><b>{props.slots[Number(props.slot)].label}</b></div><button type="button" onClick={props.onSaveNext}><span>현재 구간 저장</span><b>다음 15분으로 이동 →</b></button></footer>
        </aside>
      </div>
    </section>
  );
}
