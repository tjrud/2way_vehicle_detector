"use client";

// 선택한 원본 영상 목록을 바탕으로 GPU·CPU별 FFmpeg 전처리 명령을 생성한다.

import { useMemo, useRef, useState } from "react";

type VideoItem = { name: string; size: number; lastModified: number };
type Encoder = "nvidia" | "amd" | "cpu";

const formatBytes = (bytes: number) => {
  if (!bytes) return "0 GB";
  return `${(bytes / 1024 / 1024 / 1024).toFixed(bytes > 10 * 1024 ** 3 ? 1 : 2)} GB`;
};

const naturalSort = (left: VideoItem, right: VideoItem) => left.name.localeCompare(right.name, "ko", { numeric: true });
const psQuote = (value: string) => `'${value.replaceAll("'", "''")}'`;

export default function PreprocessWorkspace({ onOpenCounter }: { onOpenCounter: () => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [folderName, setFolderName] = useState("");
  const [startFile, setStartFile] = useState("");
  const [startOffset, setStartOffset] = useState("00:00:00");
  const [encoder, setEncoder] = useState<Encoder>("nvidia");
  const [markers, setMarkers] = useState(true);
  const [status, setStatus] = useState<"idle" | "ready" | "downloaded">("idle");

  const totalSize = useMemo(() => videos.reduce((sum, video) => sum + video.size, 0), [videos]);
  const startIndex = Math.max(0, videos.findIndex(({ name }) => name === startFile));

  const loadFiles = (files: FileList | null) => {
    if (!files) return;
    const selected = Array.from(files)
      .filter((file) => /\.(mp4|mov|m4v|avi)$/i.test(file.name))
      .map((file) => ({ name: file.name, size: file.size, lastModified: file.lastModified }))
      .sort(naturalSort);
    setVideos(selected);
    setFolderName(files[0]?.webkitRelativePath?.split("/")[0] ?? "선택한 영상 폴더");
    setStartFile(selected[0]?.name ?? "");
    setStatus(selected.length ? "ready" : "idle");
  };

  const offsetSeconds = () => {
    const parts = startOffset.split(":").map(Number);
    if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part) || part < 0)) return null;
    return parts[0] * 3600 + parts[1] * 60 + parts[2];
  };

  const buildScript = () => {
    const offset = offsetSeconds();
    if (!videos.length || offset === null) return null;
    const ordered = [...videos.slice(startIndex), ...videos.slice(0, startIndex)];
    const sources: Array<{ name: string; start?: number; end?: number }> = offset > 0
      ? [{ name: ordered[0].name, start: offset }, ...ordered.slice(1), { name: ordered[0].name, end: offset }]
      : ordered;
    const sourceLines = sources.map((source) => `    @{ Name = ${psQuote(source.name)}; Start = ${source.start ?? "$null"}; End = ${source.end ?? "$null"} }`);
    const encoderArgs = encoder === "nvidia"
      ? "'-c:v','h264_nvenc','-preset','p4','-rc','vbr','-cq','23','-b:v','0'"
      : encoder === "amd"
        ? "'-c:v','h264_amf','-usage','transcoding','-quality','balanced','-rc','cqp','-qp_i','22','-qp_p','23','-qp_b','25'"
        : "'-c:v','libx264','-preset','veryfast','-crf','23'";
    const markerFilters = markers
      ? ",drawbox=x=0:y=ih-90:w=iw:h=90:color=black@0.65:t=fill:enable='lt(mod(t\\,300)\\,5)',drawbox=x=0:y=0:w=iw:h=ih:color=yellow@0.95:t=14:enable='lt(mod(t\\,300)\\,5)',drawtext=fontfile='C\\:/Windows/Fonts/malgun.ttf':text='15분 단위 기록 시점':fontcolor=yellow:fontsize=36:x=(w-text_w)/2:y=h-63:enable='lt(mod(t\\,300)\\,5)'"
      : "";
    return [
      "$ErrorActionPreference = 'Stop'",
      "Add-Type -AssemblyName System.Windows.Forms",
      "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
      "$dialog.Description = '원본 영상 폴더를 선택하세요'",
      "if ($dialog.ShowDialog() -ne 'OK') { exit }",
      "$sourceDir = $dialog.SelectedPath",
      "$dialog.Description = '전처리 결과를 저장할 폴더를 선택하세요'",
      "if ($dialog.ShowDialog() -ne 'OK') { exit }",
      "$outputDir = $dialog.SelectedPath",
      "$ffmpegCommand = Get-Command ffmpeg -ErrorAction SilentlyContinue",
      "if (-not $ffmpegCommand) { [System.Windows.Forms.MessageBox]::Show('FFmpeg가 설치되어 있지 않습니다. 먼저 winget install Gyan.FFmpeg 를 실행하세요.'); exit 1 }",
      "$ffmpeg = $ffmpegCommand.Source",
      "$sources = @(",
      ...sourceLines,
      ")",
      "$args = @('-hide_banner','-y')",
      "$filters = @()",
      "for ($i=0; $i -lt $sources.Count; $i++) {",
      "  $source = $sources[$i]",
      "  $path = Join-Path $sourceDir $source.Name",
      "  if (-not (Test-Path -LiteralPath $path)) { throw \"파일 없음: $path\" }",
      "  $args += @('-i',$path)",
      "  $trim = @()",
      "  if ($null -ne $source.Start) { $trim += \"start=$($source.Start)\" }",
      "  if ($null -ne $source.End) { $trim += \"end=$($source.End)\" }",
      "  $prefix = if ($trim.Count) { 'trim=' + ($trim -join ':') + ',' } else { '' }",
      "  $filters += \"[$i`:v]$prefix" + "setpts=PTS-STARTPTS[v$i]\"",
      "}",
      "$inputs = ((0..($sources.Count-1)) | ForEach-Object { \"[v$_]\" }) -join ''",
      "$filters += $inputs + \"concat=n=$($sources.Count):v=1:a=0[combined]\"",
      '$filters += "[combined]trim=start=0:duration=86400,setpts=(PTS-STARTPTS)/3,fps=30000/1001' + markerFilters + '[v]"',
      "$audioIndex = $sources.Count",
      '$filters += "[$audioIndex`:a]volume=\'if(gte(t\\,299.9)*lt(mod(t\\,300)\\,0.35)\\,0.5\\,0)\':eval=frame[a]"',
      "$filterComplex = $filters -join ';'",
      "$rawPattern = Join-Path $outputDir 'segment_%02d.mp4'",
      "$args += @('-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=28800','-filter_complex',$filterComplex,'-map','[v]','-map','[a]','-t','28800')",
      '$args += @(' + encoderArgs + ",'-pix_fmt','yuv420p','-r','30000/1001','-force_key_frames','expr:gte(t,n_forced*3600)','-c:a','aac','-b:a','64k','-f','segment','-segment_time','3600','-segment_start_number','0','-reset_timestamps','1','-segment_format_options','movflags=+faststart','-progress','pipe:1','-stats_period','30','-nostats',$rawPattern)",
      "& $ffmpeg @args",
      "if ($LASTEXITCODE -ne 0) { throw \"전처리 실패: $LASTEXITCODE\" }",
      "$segments = @(Get-ChildItem -LiteralPath $outputDir -Filter 'segment_*.mp4' | Sort-Object Name)",
      "if ($segments.Count -ne 8) { throw \"결과 파일이 8개가 아닙니다: $($segments.Count)개\" }",
      "for ($i=0; $i -lt 8; $i++) {",
      "  $startHour = $i * 3; $endHour = ($i + 1) * 3",
      "  $name = '{0:D2}_{1:D2}00-{2:D2}00_3배속.mp4' -f ($i+1),$startHour,$endHour",
      "  Rename-Item -LiteralPath $segments[$i].FullName -NewName $name",
      "}",
      "[System.Windows.Forms.MessageBox]::Show('24시간 영상 전처리가 완료되었습니다.')",
    ].join("\r\n");
  };

  const downloadScript = () => {
    const script = buildScript();
    if (!script) return;
    const blob = new Blob(["\uFEFF", script], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `차량영상_24시간_전처리_${new Date().toISOString().slice(0, 10)}.ps1`;
    link.click();
    URL.revokeObjectURL(url);
    setStatus("downloaded");
  };

  return (
    <section className="preprocess-workspace">
      <header className="workspace-heading"><div><p>VIDEO PREPARATION</p><h1>영상 전처리 작업실</h1><span>24시간 원본을 3배속 영상 8개로 만들고, 15분 기록 시점을 영상에 표시합니다.</span></div><button type="button" onClick={onOpenCounter}>카운팅 화면 열기 →</button></header>
      <div className="preprocess-grid">
        <section className="preprocess-card source-card"><div className="step-number">01</div><header><div><b>원본 폴더 선택</b><span>GoPro 원본 영상이 들어 있는 폴더</span></div><strong className={videos.length ? "complete" : ""}>{videos.length ? "선택 완료" : "대기"}</strong></header><button type="button" className="folder-picker" onClick={() => inputRef.current?.click()}><span>▣</span><div><b>{folderName || "영상 폴더를 선택하세요"}</b><small>{videos.length ? `${videos.length}개 영상 · ${formatBytes(totalSize)}` : "MP4 · MOV · M4V · AVI"}</small></div><em>폴더 선택</em></button><input ref={inputRef} className="hidden-folder-input" type="file" multiple accept="video/*,.mp4,.mov,.m4v,.avi" onChange={(event) => loadFiles(event.target.files)} {...({ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>)} />{videos.length > 0 && <div className="video-file-list">{videos.slice(0, 8).map((video, index) => <div key={video.name}><span>{String(index + 1).padStart(2, "0")}</span><b>{video.name}</b><small>{formatBytes(video.size)}</small></div>)}{videos.length > 8 && <p>외 {videos.length - 8}개 영상</p>}</div>}</section>
        <section className="preprocess-card settings-card"><div className="step-number">02</div><header><div><b>하루 시작점 설정</b><span>00:00에 해당하는 영상과 영상 내부 시점</span></div><strong className={videos.length ? "complete" : ""}>{videos.length ? "설정 가능" : "폴더 필요"}</strong></header><label><span>첫 번째 영상</span><select disabled={!videos.length} value={startFile} onChange={(event) => setStartFile(event.target.value)}>{videos.map((video) => <option key={video.name} value={video.name}>{video.name}</option>)}</select></label><label><span>시작 시점 · 시:분:초</span><input disabled={!videos.length} value={startOffset} placeholder="00:00:00" onChange={(event) => setStartOffset(event.target.value)} /></label><div className="encoder-options"><span>영상 인코더</span>{(["nvidia", "amd", "cpu"] as Encoder[]).map((value) => <button type="button" key={value} className={encoder === value ? "selected" : ""} onClick={() => setEncoder(value)}>{value === "nvidia" ? "NVIDIA" : value === "amd" ? "AMD" : "CPU"}</button>)}</div><label className="marker-toggle"><div><b>15분 기록 알림</b><small>5분마다 노란 테두리와 알림음 표시</small></div><input type="checkbox" checked={markers} onChange={(event) => setMarkers(event.target.checked)} /><i /></label></section>
        <section className="preprocess-card output-card"><div className="step-number">03</div><header><div><b>전처리 실행</b><span>Windows용 FFmpeg 실행 파일 생성</span></div><strong className={status === "downloaded" ? "complete" : ""}>{status === "downloaded" ? "준비 완료" : "대기"}</strong></header><div className="output-summary"><div><span>원본 범위</span><b>24시간</b></div><div><span>재생 속도</span><b>3배속</b></div><div><span>결과</span><b>8개 파일</b></div><div><span>파일당 실제 시간</span><b>3시간</b></div></div><button type="button" className="preprocess-run" disabled={!videos.length || offsetSeconds() === null} onClick={downloadScript}><span>전처리 실행 파일 만들기</span><b>PowerShell · FFmpeg</b></button><p className="browser-limit"><b>실행 방법</b> 다운로드한 파일을 우클릭해 PowerShell로 실행한 뒤, Windows 폴더 창에서 원본 폴더와 저장 폴더를 선택하세요.</p></section>
      </div>
      <section className="workflow-note"><b>전처리 순서</b><span>시작 영상의 지정 시점부터 파일 순서를 이어 붙이고 → 24시간으로 자르고 → 3배속 변환 → 15분 시점 표시 → 1시간 길이 영상 8개로 저장합니다.</span></section>
    </section>
  );
}
