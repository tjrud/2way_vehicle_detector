"use client";

// 대시보드 전체 작업공간 전환, 수동 집계 상태, 기록 슬롯과 내보내기를 조정한다.
// 세부 기능은 AI 분석·전처리·라이브 집계 하위 컴포넌트로 분리한다.

import { useEffect, useMemo, useRef, useState } from "react";
import JSZip from "jszip";
import { approachKeys, customPreset, defaultCustomConfig, normalizeCustomConfig, resizeApproach } from "./custom-layout.mjs";
import { summarizeTwoWayHours } from "./excel-mapping.mjs";
import { createEmptyLibrary, migrateToLibrary, shiftRecords } from "./record-storage.mjs";
import PreprocessWorkspace from "./PreprocessWorkspace";
import LiveCountingWorkspace from "./LiveCountingWorkspace";
import AdminWorkspace from "./AdminWorkspace";
import AiAnalysisWorkspace from "./AiAnalysisWorkspace";

const fullMode = [
  { id: 9, area: "n1" }, { id: 8, area: "n2" }, { id: 7, area: "n3" },
  { id: 10, area: "w1" }, { id: 11, area: "w2" }, { id: 12, area: "w3" },
  { id: 6, area: "e1" }, { id: 5, area: "e2" }, { id: 4, area: "e3" },
  { id: 1, area: "s1" }, { id: 2, area: "s2" }, { id: 3, area: "s3" },
] as const;
const photoMode = [
  { id: 3, area: "p3" }, { id: 2, area: "p2" }, { id: 4, area: "p4" },
  { id: 6, area: "p6" }, { id: 7, area: "p7" }, { id: 8, area: "p8" },
] as const;
const boxMode = [
  { id: 3, area: "n1" }, { id: 2, area: "n2" }, { id: 1, area: "n3" },
  { id: 4, area: "w1" }, { id: 5, area: "w2" }, { id: 6, area: "w3" },
  { id: 12, area: "e1" }, { id: 11, area: "e2" }, { id: 10, area: "e3" },
  { id: 7, area: "s1" }, { id: 8, area: "s2" }, { id: 9, area: "s3" },
] as const;
const gyuhoMode = [
  { id: 6, area: "n1" }, { id: 5, area: "n2" }, { id: 4, area: "n3" },
  { id: 7, area: "w1" }, { id: 8, area: "w2" }, { id: 9, area: "w3" },
  { id: 3, area: "e1" }, { id: 2, area: "e2" }, { id: 1, area: "e3" },
  { id: 10, area: "s1" }, { id: 11, area: "s2" }, { id: 12, area: "s3" },
] as const;
const twoWayMode = [
  { id: 1, area: "tw-in" },
  { id: 2, area: "tw-out" },
] as const;

type Mode = "full" | "photo" | "box" | "gyuho" | "twoway" | "custom";
type Movement = "left" | "straight" | "right";
type Approach = "north" | "east" | "south" | "west";
type CustomLane = { id: number; movement: Movement };
type CustomConfig = { name: string; approaches: Record<Approach, CustomLane[]> };
type VehicleCategory = "passenger" | "busSmall" | "busLarge" | "truckSmall" | "truckLarge" | "trailer";
type Theme = "light" | "dark" | "green";
type InputStyle = "card" | "buttons";
type SoundName = "click" | "clack" | "soft";
type CounterSound = SoundName | "default";
type CounterSounds = Record<Mode, Record<number, CounterSound>>;
type Counts = Record<string, number>;
type Records = Record<string, Counts>;
type Drafts = Record<string, Counts>;
type ClickLog = { t: number; s: string; n: number; v?: VehicleCategory; m?: Movement; d: -1 | 1; b: number; a: number };
type RecordSet = { id: string; name: string; records: Records; drafts: Drafts; slot: string; clickLogs: ClickLog[]; customConfig?: CustomConfig };
type RecordLibrary = Record<Mode, RecordSet[]>;
type ActiveRecordIds = Record<Mode, string>;
type ExcelState = { kind: "idle" | "working" | "success" | "error"; message: string };
type CorrectionState = { kind: "idle" | "success" | "error"; message: string };

const emptyCounts = (): Counts =>
  Object.fromEntries(Array.from({ length: 12 }, (_, i) => [i + 1, 0])) as Counts;
const pad = (value: number) => String(value).padStart(2, "0");
const slots = Array.from({ length: 96 }, (_, index) => {
  const start = index * 15;
  const end = start + 15;
  return {
    key: pad(index),
    label: `${pad(Math.floor(start / 60))}:${pad(start % 60)} ~ ${pad(Math.floor(end / 60) % 24)}:${pad(end % 60)}`,
  };
});
const excelColumns = ["H", "P", "X"] as const;
const vehicleCategories: Array<{ key: VehicleCategory; group: string; label: string }> = [
  { key: "passenger", group: "소형", label: "승용" },
  { key: "busSmall", group: "버스", label: "소형" },
  { key: "busLarge", group: "버스", label: "대형" },
  { key: "truckSmall", group: "화물", label: "소형" },
  { key: "truckLarge", group: "화물", label: "중·대형" },
  { key: "trailer", group: "화물", label: "트레일러" },
];
const vehicleKeys = vehicleCategories.map(({ key }) => key);
const vehicleCountKey = (id: number, category: VehicleCategory) => `${id}:${category}`;
const vehicleLabel = (category: VehicleCategory) => {
  const item = vehicleCategories.find(({ key }) => key === category)!;
  return item.key === "passenger" ? item.label : `${item.group} ${item.label}`;
};
const soundNames: SoundName[] = ["click", "clack", "soft"];
const counterIdsByMode: Record<Mode, number[]> = {
  full: Array.from({ length: 12 }, (_, index) => index + 1),
  photo: [2, 3, 4, 6, 7, 8],
  box: Array.from({ length: 12 }, (_, index) => index + 1),
  gyuho: Array.from({ length: 12 }, (_, index) => index + 1),
  twoway: [1, 2],
  custom: [],
};
const emptyCounterSounds = (): CounterSounds => ({
  full: Object.fromEntries(counterIdsByMode.full.map((id) => [id, "default"])) as Record<number, CounterSound>,
  photo: Object.fromEntries(counterIdsByMode.photo.map((id) => [id, "default"])) as Record<number, CounterSound>,
  box: Object.fromEntries(counterIdsByMode.box.map((id) => [id, "default"])) as Record<number, CounterSound>,
  gyuho: Object.fromEntries(counterIdsByMode.gyuho.map((id) => [id, "default"])) as Record<number, CounterSound>,
  twoway: Object.fromEntries(counterIdsByMode.twoway.map((id) => [id, "default"])) as Record<number, CounterSound>,
  custom: {},
});
const normalizeCounterSounds = (value: unknown): CounterSounds => {
  const normalized = emptyCounterSounds();
  if (!value || typeof value !== "object") return normalized;
  for (const modeName of ["full", "photo", "box", "gyuho", "twoway", "custom"] as Mode[]) {
    const source = (value as Partial<Record<Mode, Record<number, unknown>>>)[modeName];
    if (!source || typeof source !== "object") continue;
    const ids = modeName === "custom" ? Object.keys(source).map(Number).filter((id) => Number.isInteger(id) && id >= 1 && id <= 99) : counterIdsByMode[modeName];
    ids.forEach((id) => {
      const selected = source[id];
      if (["default", ...soundNames].includes(selected as CounterSound)) normalized[modeName][id] = selected as CounterSound;
    });
  }
  return normalized;
};
const soundLabel = (name: SoundName) => name === "click" ? "클릭" : name === "clack" ? "딸칵" : "부드러운 톤";
const movementOf = (id: number): Movement => id % 3 === 1 ? "left" : id % 3 === 2 ? "straight" : "right";
const movementName = (movement: Movement) => movement === "left" ? "좌회전" : movement === "straight" ? "직진" : "우회전";
const modeName = (mode: Mode) => mode === "full" ? "모드 1 · 12개" : mode === "photo" ? "모드 2 · 6개" : mode === "box" ? "모드 3 · 12개" : mode === "gyuho" ? "규호 모드 · 차량 분류" : mode === "twoway" ? "2way 모드 · 유입/유출" : "커스텀 모드";
const modeFileName = (mode: Mode) => mode === "full" ? "모드1_12개" : mode === "photo" ? "모드2_6개" : mode === "box" ? "모드3_12개" : mode === "gyuho" ? "규호모드_차량분류" : mode === "twoway" ? "2way모드_유입유출" : "커스텀교차로";
const twoWayLabel = (id: number) => id === 1 ? "유입" : "유출";
const approachName = (approach: Approach) => approach === "north" ? "상단" : approach === "east" ? "우측" : approach === "south" ? "하단" : "좌측";

const parseXml = (text: string) => {
  const document = new DOMParser().parseFromString(text, "application/xml");
  if (document.querySelector("parsererror")) throw new Error("엑셀 내부 문서를 읽을 수 없습니다.");
  return document;
};

const numericCellValue = (cell: Element | null) => {
  if (!cell) return null;
  const value = Number(cell.querySelector("v")?.textContent);
  return Number.isFinite(value) ? value : null;
};

const textCellValue = (cell: Element | null, sharedStrings: string[]) => {
  if (!cell) return "";
  const type = cell.getAttribute("t");
  if (type === "s") {
    const index = Number(cell.querySelector("v")?.textContent);
    return Number.isInteger(index) ? sharedStrings[index] ?? "" : "";
  }
  if (type === "inlineStr") return cell.querySelector("is")?.textContent ?? "";
  return cell.querySelector("v")?.textContent ?? "";
};

const setNumericCell = (document: XMLDocument, row: Element, column: string, rowNumber: number, value: number) => {
  const address = `${column}${rowNumber}`;
  let cell = Array.from(row.children).find((item) => item.tagName.endsWith("c") && item.getAttribute("r") === address);
  if (!cell) {
    cell = document.createElementNS(row.namespaceURI, "c");
    cell.setAttribute("r", address);
    const columnNumber = column.charCodeAt(0) - 64;
    const nextCell = Array.from(row.children).find((item) => {
      const reference = item.getAttribute("r") ?? "";
      return reference.charCodeAt(0) - 64 > columnNumber;
    });
    row.insertBefore(cell, nextCell ?? null);
  }
  cell.removeAttribute("t");
  Array.from(cell.children).forEach((child) => {
    if (child.tagName.endsWith("f") || child.tagName.endsWith("v") || child.tagName.endsWith("is")) child.remove();
  });
  const valueNode = document.createElementNS(row.namespaceURI, "v");
  valueNode.textContent = String(Math.max(0, Math.trunc(value)));
  cell.appendChild(valueNode);
};

export default function DashboardClient({ user, authProvider }: { user: { displayName: string; email: string; role: "admin" | "user" }; authProvider: "chatgpt" | "password" }) {
  const [workspaceView, setWorkspaceView] = useState<"home" | "preprocess" | "ai" | "live" | "counter" | "admin">("home");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const mode: Mode = "custom";
  const [library, setLibrary] = useState<RecordLibrary>(() => createEmptyLibrary().library as RecordLibrary);
  const [activeRecordIds, setActiveRecordIds] = useState<ActiveRecordIds>(() => createEmptyLibrary().activeRecordIds as ActiveRecordIds);
  const [ready, setReady] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [showSheet, setShowSheet] = useState(false);
  const [sheetView, setSheetView] = useState<"summary" | "clicks">("summary");
  const [showSettings, setShowSettings] = useState(false);
  const [showCustomSettings, setShowCustomSettings] = useState(false);
  const [customConfig, setCustomConfig] = useState<CustomConfig>(() => defaultCustomConfig() as CustomConfig);
  const [customDraft, setCustomDraft] = useState<CustomConfig>(() => defaultCustomConfig() as CustomConfig);
  const [customConfigError, setCustomConfigError] = useState("");
  const [copyState, setCopyState] = useState("표 복사");
  const [theme, setTheme] = useState<Theme>("light");
  const [inputStyle, setInputStyle] = useState<InputStyle>("card");
  const [selectedVehicle, setSelectedVehicle] = useState<VehicleCategory>("passenger");
  const [soundOn, setSoundOn] = useState(false);
  const [soundName, setSoundName] = useState<SoundName>("click");
  const [counterSounds, setCounterSounds] = useState<CounterSounds>(emptyCounterSounds);
  const [volume, setVolume] = useState(60);
  const [excelFile, setExcelFile] = useState<File | null>(null);
  const [excelState, setExcelState] = useState<ExcelState>({ kind: "idle", message: "" });
  const [editingRecordName, setEditingRecordName] = useState(false);
  const [recordNameDraft, setRecordNameDraft] = useState("");
  const [confirmDeleteRecord, setConfirmDeleteRecord] = useState(false);
  const [showCorrection, setShowCorrection] = useState(false);
  const [correctionStart, setCorrectionStart] = useState("00");
  const [correctionEnd, setCorrectionEnd] = useState("00");
  const [correctionOffset, setCorrectionOffset] = useState<-1 | 1>(-1);
  const [correctionState, setCorrectionState] = useState<CorrectionState>({ kind: "idle", message: "" });
  const [correctionUndo, setCorrectionUndo] = useState<Records | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const compressorRef = useRef<DynamicsCompressorNode | null>(null);
  const nextSoundAtRef = useRef(0);
  const sidebarSettingsRef = useRef<HTMLDetailsElement | null>(null);

  useEffect(() => {
    if (user.role === "admin") return;
    const viewLabels = { home: "HOME", preprocess: "영상 전처리", ai: "AI 차량 분석", live: "실시간 영상 계수", counter: "차량 카운팅", admin: "관리자 페이지" } as const;
    void fetch("/api/usage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ eventType: "page_view", detail: viewLabels[workspaceView] }),
      keepalive: true,
    }).catch(() => undefined);
  }, [workspaceView, user.role]);

  useEffect(() => {
    try {
      const saved = localStorage.getItem("intersection-timed-records-v3") ?? localStorage.getItem("intersection-timed-records-v2") ?? localStorage.getItem("intersection-timed-records-v1");
      if (saved) {
        const parsed = JSON.parse(saved);
        const migrated = migrateToLibrary(parsed);
        // eslint-disable-next-line react-hooks/set-state-in-effect -- 브라우저에 저장된 현장 기록을 최초 한 번 복원합니다.
        setLibrary(migrated.library as RecordLibrary);
        setActiveRecordIds(migrated.activeRecordIds as ActiveRecordIds);
        const customSets = (migrated.library as RecordLibrary).custom;
        const selectedCustomId = (migrated.activeRecordIds as ActiveRecordIds).custom;
        const selectedCustomSet = customSets.find((recordSet) => recordSet.id === selectedCustomId) ?? customSets[0];
        const restoredCustom = normalizeCustomConfig(selectedCustomSet.customConfig ?? parsed.customConfig) as CustomConfig;
        setCustomConfig(restoredCustom);
        setCustomDraft(restoredCustom);
        setTheme(["light", "dark", "green"].includes(parsed.theme) ? parsed.theme : "light");
        setInputStyle(parsed.inputStyle === "buttons" ? "buttons" : "card");
        setSelectedVehicle(vehicleKeys.includes(parsed.selectedVehicle) ? parsed.selectedVehicle : "passenger");
        setSoundOn(Boolean(parsed.soundOn));
        setSoundName(["click", "clack", "soft"].includes(parsed.soundName) ? parsed.soundName : "click");
        setCounterSounds(normalizeCounterSounds(parsed.counterSounds));
        setVolume(typeof parsed.volume === "number" ? Math.min(100, Math.max(0, parsed.volume)) : 60);
      }
    } catch {
      // Start with a clean record when browser storage cannot be read.
    }
    setReady(true);
  }, []);

  useEffect(() => {
    if (ready) localStorage.setItem("intersection-timed-records-v3", JSON.stringify({ dataVersion: 4, library, activeRecordIds, mode, customConfig, theme, inputStyle, selectedVehicle, soundOn, soundName, counterSounds, volume }));
  }, [library, activeRecordIds, mode, customConfig, theme, inputStyle, selectedVehicle, soundOn, soundName, counterSounds, volume, ready]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => () => {
    audioContextRef.current?.close();
    audioContextRef.current = null;
    compressorRef.current = null;
  }, []);

  const customPositions = useMemo(() => (approachKeys as Approach[]).flatMap((approach) => customConfig.approaches[approach].map((lane) => ({ ...lane, approach, area: `custom-${approach}` }))), [customConfig]);
  const positions = customPositions;
  const ids = customPositions.map(({ id }) => id);
  const isBoxMode = false;
  const isGyuhoMode = false;
  const isTwoWayMode = false;
  const isCustomMode = true;
  const usesCardControls = inputStyle === "card";
  const movementForCounter = (id: number) => customPositions.find((lane) => lane.id === id)?.movement ?? "straight";
  const soundConfigIds = customPositions.map(({ id }) => id);
  const modeRecordSets = library[mode];
  const activeRecordId = activeRecordIds[mode];
  const activeRecordSet = modeRecordSets.find((recordSet) => recordSet.id === activeRecordId) ?? modeRecordSets[0];
  const { records, drafts, slot } = activeRecordSet;
  const safeRecordName = activeRecordSet.name.replace(/[\\/:*?"<>|]/g, "_");
  const counts = drafts[slot] ?? records[slot] ?? emptyCounts();
  const total = useMemo(() => positions.reduce((sum, { id }) => sum + (counts[id] ?? 0), 0), [counts, positions]);
  const tableColumns = ids.map((id) => ({ key: String(id), label: `${id}번 ${movementName(movementForCounter(id))}` }));
  const savedCurrent = Boolean(records[slot]);
  const homeSavedSlots = Object.keys(records).length;
  const homeVehicleTotal = Object.values(records).reduce((totalValue, row) => totalValue + Object.values(row).reduce((rowTotal, value) => rowTotal + value, 0), 0);
  const homeRecentLogs = [...(activeRecordSet.clickLogs ?? [])].slice(-5).reverse();
  const updateActiveRecordSet = (update: (recordSet: RecordSet) => RecordSet) => {
    setLibrary((current) => ({
      ...current,
      [mode]: current[mode].map((recordSet) => recordSet.id === activeRecordId ? update(recordSet) : recordSet),
    }));
  };
  const setCurrentCounts = (next: Counts) => updateActiveRecordSet((recordSet) => ({ ...recordSet, drafts: { ...recordSet.drafts, [slot]: next } }));
  const selectSlot = (nextSlot: string) => updateActiveRecordSet((recordSet) => ({ ...recordSet, slot: nextSlot }));
  const selectRecordSet = (recordSetId: string) => {
    setActiveRecordIds((current) => ({ ...current, [mode]: recordSetId }));
    if (isCustomMode) {
      const selected = modeRecordSets.find((recordSet) => recordSet.id === recordSetId);
      const nextConfig = normalizeCustomConfig(selected?.customConfig ?? customConfig) as CustomConfig;
      setCustomConfig(nextConfig);
      setCustomDraft(nextConfig);
    }
    setEditingRecordName(false);
    setConfirmDeleteRecord(false);
    setExcelFile(null);
    setExcelState({ kind: "idle", message: "" });
    setShowCorrection(false);
    setCorrectionUndo(null);
  };
  const createRecordSet = () => {
    const nextNumber = modeRecordSets.length + 1;
    const id = `${mode}-${crypto.randomUUID()}`;
    const nextRecordSet: RecordSet = { id, name: `기록 ${nextNumber}`, records: {}, drafts: {}, slot: "00", clickLogs: [], ...(isCustomMode ? { customConfig: normalizeCustomConfig(customConfig) as CustomConfig } : {}) };
    setLibrary((current) => ({ ...current, [mode]: [...current[mode], nextRecordSet] }));
    setActiveRecordIds((current) => ({ ...current, [mode]: id }));
    setEditingRecordName(false);
    setConfirmDeleteRecord(false);
    setExcelFile(null);
    setExcelState({ kind: "idle", message: "" });
  };
  const openCustomEditor = () => {
    setCustomDraft(normalizeCustomConfig(activeRecordSet.customConfig ?? customConfig) as CustomConfig);
    setCustomConfigError("");
    setShowCustomSettings(true);
  };
  const loadCustomPreset = (preset: "full" | "photo" | "box" | "gyuho" | "blank") => {
    setCustomDraft(customPreset(preset) as CustomConfig);
    setCustomConfigError("");
  };
  const changeApproachCount = (approach: Approach, count: number) => {
    setCustomDraft((current) => resizeApproach(current, approach, count) as CustomConfig);
    setCustomConfigError("");
  };
  const updateCustomLane = (approach: Approach, index: number, update: Partial<CustomLane>) => {
    setCustomDraft((current) => ({
      ...current,
      approaches: { ...current.approaches, [approach]: current.approaches[approach].map((lane, laneIndex) => laneIndex === index ? { ...lane, ...update } : lane) },
    }));
    setCustomConfigError("");
  };
  const saveCustomConfig = () => {
    const lanes = (approachKeys as Approach[]).flatMap((approach) => customDraft.approaches[approach]);
    if (lanes.length === 0) {
      setCustomConfigError("차선을 1개 이상 추가해 주세요.");
      return;
    }
    if (lanes.some(({ id }) => !Number.isInteger(id) || id < 1 || id > 99)) {
      setCustomConfigError("차선 번호는 1부터 99 사이의 정수로 입력해 주세요.");
      return;
    }
    if (new Set(lanes.map(({ id }) => id)).size !== lanes.length) {
      setCustomConfigError("같은 번호를 두 차선에 사용할 수 없습니다.");
      return;
    }
    const nextConfig = normalizeCustomConfig(customDraft) as CustomConfig;
    setCustomConfig(nextConfig);
    updateActiveRecordSet((recordSet) => ({ ...recordSet, customConfig: nextConfig }));
    setCounterSounds((current) => ({ ...current, custom: { ...current.custom, ...Object.fromEntries(lanes.map(({ id }) => [id, current.custom[id] ?? "default"])) } }));
    setShowCustomSettings(false);
  };
  const startRenamingRecord = () => {
    setRecordNameDraft(activeRecordSet.name);
    setEditingRecordName(true);
    setConfirmDeleteRecord(false);
  };
  const saveRecordName = () => {
    const nextName = recordNameDraft.trim();
    if (!nextName) return;
    updateActiveRecordSet((recordSet) => ({ ...recordSet, name: nextName }));
    setEditingRecordName(false);
  };
  const requestDeleteRecord = () => {
    setEditingRecordName(false);
    setConfirmDeleteRecord(true);
  };
  const deleteRecordSet = () => {
    if (modeRecordSets.length === 1) {
      const replacementId = `${mode}-${crypto.randomUUID()}`;
      const replacement: RecordSet = { id: replacementId, name: "기록 1", records: {}, drafts: {}, slot: "00", clickLogs: [], ...(isCustomMode ? { customConfig: normalizeCustomConfig(customConfig) as CustomConfig } : {}) };
      setLibrary((current) => ({ ...current, [mode]: [replacement] }));
      setActiveRecordIds((current) => ({ ...current, [mode]: replacementId }));
    } else {
      const currentIndex = modeRecordSets.findIndex((recordSet) => recordSet.id === activeRecordId);
      const nextRecordSet = modeRecordSets[currentIndex > 0 ? currentIndex - 1 : 1];
      setLibrary((current) => ({ ...current, [mode]: current[mode].filter((recordSet) => recordSet.id !== activeRecordId) }));
      setActiveRecordIds((current) => ({ ...current, [mode]: nextRecordSet.id }));
      if (isCustomMode) {
        const nextConfig = normalizeCustomConfig(nextRecordSet.customConfig ?? customConfig) as CustomConfig;
        setCustomConfig(nextConfig);
        setCustomDraft(nextConfig);
      }
    }
    setConfirmDeleteRecord(false);
    setExcelFile(null);
    setExcelState({ kind: "idle", message: "" });
  };
  const playSound = (force = false, direction: 1 | -1 = 1, selectedSound: SoundName = soundName) => {
    if (!soundOn && !force) return;
    let context = audioContextRef.current;
    if (!context || context.state === "closed") {
      context = new AudioContext({ latencyHint: "interactive" });
      audioContextRef.current = context;
      const compressor = context.createDynamicsCompressor();
      compressor.threshold.value = -14;
      compressor.knee.value = 16;
      compressor.ratio.value = 6;
      compressor.attack.value = 0.002;
      compressor.release.value = 0.09;
      compressor.connect(context.destination);
      compressorRef.current = compressor;
    }
    if (context.state === "suspended") void context.resume();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.connect(gain);
    gain.connect(compressorRef.current ?? context.destination);
    const now = Math.max(context.currentTime + 0.003, nextSoundAtRef.current);
    nextSoundAtRef.current = now + 0.024;
    const pitch = direction === -1 ? 0.7 : 1;
    if (selectedSound === "click") {
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(760 * pitch, now);
      oscillator.frequency.exponentialRampToValueAtTime(420 * pitch, now + 0.045);
      gain.gain.setValueAtTime(0.55 * (volume / 100), now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.055);
      oscillator.start(now); oscillator.stop(now + 0.06);
    } else if (selectedSound === "clack") {
      oscillator.type = "square";
      oscillator.frequency.setValueAtTime(190 * pitch, now);
      oscillator.frequency.exponentialRampToValueAtTime(95 * pitch, now + 0.065);
      gain.gain.setValueAtTime(0.42 * (volume / 100), now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);
      oscillator.start(now); oscillator.stop(now + 0.085);
    } else {
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(440 * pitch, now);
      gain.gain.setValueAtTime(0.36 * (volume / 100), now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
      oscillator.start(now); oscillator.stop(now + 0.13);
    }
  };

  const changeCount = (id: number, amount: number) => {
    const countKey = isGyuhoMode ? vehicleCountKey(id, selectedVehicle) : String(id);
    if (amount < 0 && (counts[countKey] ?? 0) === 0) return;
    const configuredSound = counterSounds[mode][id] ?? "default";
    playSound(false, amount < 0 ? -1 : 1, configuredSound === "default" ? soundName : configuredSound);
    updateActiveRecordSet((recordSet) => {
      const base = recordSet.drafts[slot] ?? recordSet.records[slot] ?? emptyCounts();
      const before = base[countKey] ?? 0;
      const after = Math.max(0, before + amount);
      const clickLog: ClickLog = {
        t: Date.now(), s: slot, n: id, d: amount < 0 ? -1 : 1, b: before, a: after,
        ...(isGyuhoMode ? { v: selectedVehicle } : {}),
        ...((isBoxMode || isCustomMode) ? { m: movementForCounter(id) } : {}),
      };
      return {
        ...recordSet,
        drafts: { ...recordSet.drafts, [slot]: { ...base, [countKey]: after } },
        clickLogs: [...(recordSet.clickLogs ?? []), clickLog],
      };
    });
  };

  const saveAndNext = () => {
    updateActiveRecordSet((recordSet) => {
      const next = { ...recordSet.drafts };
      delete next[slot];
      return {
        ...recordSet,
        records: { ...recordSet.records, [slot]: counts },
        drafts: next,
        slot: pad((Number(slot) + 1) % 96),
      };
    });
  };

  const resetDraft = () => {
    setCurrentCounts(emptyCounts());
    setConfirmReset(false);
  };

  const openCorrection = () => {
    setCorrectionStart(slot);
    setCorrectionEnd(slot);
    setCorrectionOffset(-1);
    setCorrectionState({ kind: "idle", message: "" });
    setCorrectionUndo(null);
    setShowCorrection((current) => !current);
  };

  const applyTimeCorrection = () => {
    try {
      const shifted = shiftRecords(records, correctionStart, correctionEnd, correctionOffset) as { records: Records; moved: number };
      setCorrectionUndo(records);
      updateActiveRecordSet((recordSet) => ({ ...recordSet, records: shifted.records }));
      const direction = correctionOffset === -1 ? "앞으로" : "뒤로";
      setCorrectionState({ kind: "success", message: `${shifted.moved}개 저장 구간을 15분 ${direction} 옮겼습니다.` });
    } catch (error) {
      setCorrectionUndo(null);
      setCorrectionState({ kind: "error", message: error instanceof Error ? error.message : "시간 보정을 적용하지 못했습니다." });
    }
  };

  const undoTimeCorrection = () => {
    if (!correctionUndo) return;
    updateActiveRecordSet((recordSet) => ({ ...recordSet, records: correctionUndo }));
    setCorrectionUndo(null);
    setCorrectionState({ kind: "success", message: "방금 적용한 시간 보정을 되돌렸습니다." });
  };

  const tableText = (separator = "\t", savedOnly = false, hourlySpacing = false) => {
    const header = ["시간", ...tableColumns.map(({ label }) => label), "합계"];
    const sourceSlots = savedOnly ? slots.filter(({ key: rowSlot }) => Boolean(records[rowSlot])) : slots;
    const rows: Array<Array<string | number>> = [];
    sourceSlots.forEach(({ key: rowSlot, label }, index) => {
      const previousSlot = sourceSlots[index - 1];
      if (hourlySpacing && previousSlot && Math.floor(Number(previousSlot.key) / 4) !== Math.floor(Number(rowSlot) / 4)) {
        rows.push(Array(header.length).fill(""));
      }
      const row = records[rowSlot] ?? emptyCounts();
      const values = tableColumns.map(({ key }) => row[key] ?? 0);
      rows.push([label, ...values, values.reduce((sum, value) => sum + value, 0)]);
    });
    return [header, ...rows].map((row) => row.join(separator)).join("\n");
  };

  const copyTable = async () => {
    try {
      await navigator.clipboard.writeText(tableText());
      setCopyState("복사 완료");
      window.setTimeout(() => setCopyState("표 복사"), 1600);
    } catch {
      setCopyState("복사 실패");
    }
  };

  const downloadCsv = () => {
    const csv = `\uFEFF${tableText(",", true, true)}`;
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `차량카운트_${modeFileName(mode)}_${safeRecordName}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const formatClickTime = (timestamp: number) => {
    const date = new Date(timestamp);
    const milliseconds = String(date.getMilliseconds()).padStart(3, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${milliseconds}`;
  };

  const clickLogRows = () => (activeRecordSet.clickLogs ?? []).map((log) => [
    formatClickTime(log.t),
    slots[Number(log.s)]?.label ?? log.s,
    modeName(mode),
    activeRecordSet.name,
    isTwoWayMode ? twoWayLabel(log.n) : `${log.n}번`,
    log.v ? vehicleLabel(log.v) : log.m ? movementName(log.m) : "-",
    log.d > 0 ? "+1" : "-1",
    log.b,
    log.a,
  ]);

  const clickLogText = (separator = "\t") => {
    const header = ["클릭 시각", "기록 시간", "모드", "기록명", "번호", "방향/차량 분류", "조작", "변경 전", "변경 후"];
    return [header, ...clickLogRows()].map((row) => row.map((value) => {
      const text = String(value);
      return separator === "," && /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
    }).join(separator)).join("\n");
  };

  const copyClickLogs = async () => {
    try {
      await navigator.clipboard.writeText(clickLogText());
      setCopyState("복사 완료");
      window.setTimeout(() => setCopyState("표 복사"), 1600);
    } catch {
      setCopyState("복사 실패");
    }
  };

  const downloadClickLogs = () => {
    const csv = `\uFEFF${clickLogText(",")}`;
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `클릭로그_${modeFileName(mode)}_${safeRecordName}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const fillExcelTemplate = async () => {
    const savedSlots = Object.entries(records);
    if (isGyuhoMode) {
      setExcelState({ kind: "error", message: "규호 모드의 차량 분류 기록은 표 복사 또는 CSV 다운로드를 이용해 주세요." });
      return;
    }
    if (!excelFile) {
      setExcelState({ kind: "error", message: "먼저 수정할 .xlsx 파일을 선택해 주세요." });
      return;
    }
    if (!savedSlots.length) {
      setExcelState({ kind: "error", message: "엑셀에 입력할 저장 기록이 없습니다." });
      return;
    }

    setExcelState({ kind: "working", message: "엑셀 파일에 기록을 입력하고 있습니다…" });
    try {
      const zip = await JSZip.loadAsync(await excelFile.arrayBuffer());
      const workbookText = await zip.file("xl/workbook.xml")?.async("text");
      const relationsText = await zip.file("xl/_rels/workbook.xml.rels")?.async("text");
      if (!workbookText || !relationsText) throw new Error("올바른 .xlsx 파일이 아닙니다.");

      const workbookDocument = parseXml(workbookText);
      const relationsDocument = parseXml(relationsText);
      const sharedStringsText = await zip.file("xl/sharedStrings.xml")?.async("text");
      const sharedStrings = sharedStringsText
        ? Array.from(parseXml(sharedStringsText).querySelectorAll("si")).map((item) => item.textContent ?? "")
        : [];
      const relations = new Map(
        Array.from(relationsDocument.querySelectorAll("Relationship")).map((relation) => [
          relation.getAttribute("Id") ?? "",
          relation.getAttribute("Target") ?? "",
        ]),
      );
      const worksheetPaths = Array.from(workbookDocument.querySelectorAll("sheet")).map((sheet) => {
        const relationshipId = sheet.getAttribute("r:id") ?? sheet.getAttributeNS("http://schemas.openxmlformats.org/officeDocument/2006/relationships", "id") ?? "";
        const target = relations.get(relationshipId) ?? "";
        return target.startsWith("/") ? target.slice(1) : `xl/${target.replace(/^\.\//, "")}`;
      });

      let filledCells = 0;
      let filledHours = 0;
      let matchedTemplate = false;
      for (const worksheetPath of worksheetPaths) {
        const worksheetFile = zip.file(worksheetPath);
        if (!worksheetFile) continue;
        const worksheetDocument = parseXml(await worksheetFile.async("text"));
        const rows = new Map<number, Element>();
        worksheetDocument.querySelectorAll("row").forEach((row) => rows.set(Number(row.getAttribute("r")), row));

        if (isTwoWayMode) {
          const header = Array.from(rows.values()).find((row) => {
            const rowNumber = row.getAttribute("r");
            return textCellValue(row.querySelector(`c[r="B${rowNumber}"]`), sharedStrings).trim() === "구분"
              && textCellValue(row.querySelector(`c[r="C${rowNumber}"]`), sharedStrings).trim() === "유입"
              && textCellValue(row.querySelector(`c[r="D${rowNumber}"]`), sharedStrings).trim() === "유출";
          });
          const timeRows = Array.from(rows.entries()).map(([rowNumber, row]) => {
            const label = textCellValue(row.querySelector(`c[r="B${rowNumber}"]`), sharedStrings).replace(/\s/g, "");
            const match = label.match(/^(\d{1,2})~(\d{1,2})$/);
            return match ? { rowNumber, row, hour: Number(match[1]) } : null;
          }).filter((item): item is { rowNumber: number; row: Element; hour: number } => Boolean(item));
          if (!header || timeRows.length < 24) continue;
          matchedTemplate = true;
          const hourlyRecords = summarizeTwoWayHours(records) as Record<number, { incoming: number; outgoing: number; savedSlots: number }>;
          timeRows.forEach(({ rowNumber, row, hour }) => {
            const summary = hourlyRecords[hour];
            if (!summary) return;
            setNumericCell(worksheetDocument, row, "C", rowNumber, summary.incoming);
            setNumericCell(worksheetDocument, row, "D", rowNumber, summary.outgoing);
            filledCells += 2;
            filledHours += 1;
          });
        } else {
        const headers = Array.from(rows.entries()).filter(([, row]) => {
          const values = ["E", "M", "U"].map((column) => numericCellValue(row.querySelector(`c[r="${column}${row.getAttribute("r")}"]`)));
          return values.every((value) => value !== null && value >= 1 && value <= 12) && values[1] === values[0]! + 1 && values[2] === values[1]! + 1;
        });
        if (headers.length < 4) continue;
        matchedTemplate = true;

        headers.forEach(([headerRow, header]) => {
          const movementIds = ["E", "M", "U"].map((column) => numericCellValue(header.querySelector(`c[r="${column}${headerRow}"]`)) as number);
          const blockIndex = Math.floor((headerRow - headers[0][0]) / 112);
          const blockStartSlot = blockIndex * 16;
          savedSlots.forEach(([savedSlot, savedCounts]) => {
            const slotNumber = Number(savedSlot);
            if (slotNumber < blockStartSlot || slotNumber >= blockStartSlot + 16) return;
            const localSlot = slotNumber - blockStartSlot;
            const targetRowNumber = headerRow + 3 + Math.floor(localSlot / 4) * 6 + (localSlot % 4);
            const targetRow = rows.get(targetRowNumber);
            if (!targetRow) throw new Error(`${slots[slotNumber].label} 입력 행을 찾지 못했습니다.`);
            movementIds.forEach((movementId, index) => {
              setNumericCell(worksheetDocument, targetRow, excelColumns[index], targetRowNumber, savedCounts[movementId] ?? 0);
              filledCells += 1;
            });
          });
        });
        }

        worksheetDocument.querySelectorAll("c").forEach((cell) => {
          const hasFormula = Array.from(cell.children).some((child) => child.tagName.endsWith("f"));
          if (!hasFormula) return;
          Array.from(cell.children).forEach((child) => {
            if (child.tagName.endsWith("v")) child.remove();
          });
        });

        zip.file(worksheetPath, new XMLSerializer().serializeToString(worksheetDocument));
      }

      if (!matchedTemplate || filledCells === 0) throw new Error(isTwoWayMode ? "동두천보건소 양식의 시간·유입·유출 배치를 찾지 못했습니다." : "동연사거리 양식의 번호·시간 배치를 찾지 못했습니다.");
      const calcProperties = workbookDocument.querySelector("calcPr");
      if (calcProperties) {
        calcProperties.setAttribute("calcId", "0");
        calcProperties.setAttribute("calcMode", "auto");
        calcProperties.setAttribute("calcCompleted", "0");
        calcProperties.setAttribute("calcOnSave", "1");
        calcProperties.setAttribute("fullCalcOnLoad", "1");
        calcProperties.setAttribute("forceFullCalc", "1");
      }
      zip.file("xl/workbook.xml", new XMLSerializer().serializeToString(workbookDocument));

      relationsDocument.querySelectorAll("Relationship").forEach((relationship) => {
        if ((relationship.getAttribute("Type") ?? "").endsWith("/calcChain")) relationship.remove();
      });
      zip.file("xl/_rels/workbook.xml.rels", new XMLSerializer().serializeToString(relationsDocument));
      zip.remove("xl/calcChain.xml");

      const contentTypesFile = zip.file("[Content_Types].xml");
      if (contentTypesFile) {
        const contentTypesDocument = parseXml(await contentTypesFile.async("text"));
        contentTypesDocument.querySelectorAll("Override").forEach((override) => {
          if (override.getAttribute("PartName") === "/xl/calcChain.xml") override.remove();
        });
        zip.file("[Content_Types].xml", new XMLSerializer().serializeToString(contentTypesDocument));
      }

      const output = await zip.generateAsync({ type: "blob", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
      const url = URL.createObjectURL(output);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${excelFile.name.replace(/\.xlsx$/i, "")}_${modeFileName(mode)}_${safeRecordName}_자동입력.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
      setExcelState({ kind: "success", message: `${isTwoWayMode ? `${filledHours}개 시간` : `${savedSlots.length}개 시간대`}을 입력했습니다. 파일을 열면 합계 수식이 자동 계산됩니다.` });
    } catch (error) {
      setExcelState({ kind: "error", message: error instanceof Error ? error.message : "엑셀 파일 처리에 실패했습니다." });
    }
  };

  const renderCounter = (id: number, area: string, movementOverride?: Movement) => {
    const movement = movementOverride ?? movementOf(id);
    const countValue = isGyuhoMode ? counts[vehicleCountKey(id, selectedVehicle)] ?? 0 : counts[id] ?? 0;
    const label = isTwoWayMode ? twoWayLabel(id) : `${id}번`;
    return <article className={`counter counter-${area} ${usesCardControls ? "click-counter" : ""} ${isBoxMode ? `box-counter movement-${movement}` : ""} ${isGyuhoMode ? "gyuho-counter" : ""} ${isTwoWayMode ? `twoway-counter twoway-${id === 1 ? "in" : "out"}` : ""} ${isCustomMode ? `custom-counter movement-${movement}` : ""}`} key={`${mode}-${area}-${id}`} role={usesCardControls ? "button" : undefined} tabIndex={usesCardControls ? 0 : undefined} aria-label={usesCardControls ? `${label}${isBoxMode || isCustomMode ? ` ${movementName(movement)}` : isGyuhoMode ? ` ${vehicleLabel(selectedVehicle)}` : ""}, 좌클릭 추가, 우클릭 빼기, 현재 ${countValue}대` : undefined} onClick={usesCardControls ? () => changeCount(id, 1) : undefined} onContextMenu={usesCardControls ? (event) => { event.preventDefault(); changeCount(id, -1); } : undefined} onKeyDown={usesCardControls ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); changeCount(id, 1); } } : undefined}><span className="number-badge">{isTwoWayMode ? twoWayLabel(id) : id}</span><output aria-label={`${label} 현재 ${countValue}대`}>{countValue.toLocaleString()}</output>{usesCardControls ? <span className="counter-action-hint">{isCustomMode || isBoxMode ? movementName(movement) : isTwoWayMode ? "좌클릭 +1 · 우클릭 −1" : isGyuhoMode ? `${vehicleLabel(selectedVehicle)} · 좌 +1 · 우 −1` : "좌 +1 · 우 −1"}</span> : <div className="controls"><button type="button" className="minus" onClick={() => changeCount(id, -1)} disabled={countValue === 0} aria-label={`${label} 1대 빼기`}>−</button><button type="button" className="plus" onClick={() => changeCount(id, 1)} aria-label={`${label} 1대 추가`}>+</button></div>}</article>;
  };

  return (
    <main className={`dashboard-app ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="dashboard-sidebar">
        <div className="dashboard-brand"><span>IC</span><div><b>Intersection</b><small>CONTROL DASHBOARD</small></div></div>
        <button type="button" className="sidebar-collapse" onClick={() => setSidebarCollapsed((current) => !current)} aria-label={sidebarCollapsed ? "사이드바 펼치기" : "사이드바 접기"} title={sidebarCollapsed ? "사이드바 펼치기" : "사이드바 접기"}>{sidebarCollapsed ? "›" : "‹"}</button>
        <nav aria-label="대시보드 메뉴">
          <section className="nav-group"><h2>NAVIGATION</h2><button type="button" className={workspaceView === "home" ? "active" : ""} onClick={() => setWorkspaceView("home")}><i>⌂</i><span><b>HOME</b><small>현황 · 빠른 실행</small></span></button></section>
          <section className="nav-group"><h2>PROJECTS</h2><button type="button" className={workspaceView === "preprocess" ? "active" : ""} onClick={() => setWorkspaceView("preprocess")}><i>▶</i><span><b>영상 전처리</b><small>원본 폴더 · 3배속 변환</small></span></button><button type="button" className={workspaceView === "ai" ? "active" : ""} onClick={() => setWorkspaceView("ai")}><i>AI</i><span><b>AI 차량 분석</b><small>로컬 GPU · 유입/유출</small></span></button><button type="button" className={workspaceView === "live" ? "active" : ""} onClick={() => setWorkspaceView("live")}><i>◉</i><span><b>실시간 영상 계수</b><small>영상 확인 · 동시 계수</small></span></button><button type="button" className={workspaceView === "counter" ? "active" : ""} onClick={() => setWorkspaceView("counter")}><i>＋</i><span><b>차량 카운팅</b><small>15분 기록 · 클릭 로그</small></span></button></section>
          {user.role === "admin" && <section className="nav-group nav-admin"><h2>ADMINISTRATION</h2><button type="button" className={workspaceView === "admin" ? "active" : ""} onClick={() => setWorkspaceView("admin")}><i>◆</i><span><b>관리자 페이지</b><small>회원 목록 · 사용 기록</small></span></button></section>}
        </nav>
        <details className="dashboard-settings" ref={sidebarSettingsRef}>
          <summary aria-label="계정 및 환경 설정 열기"><span className="sidebar-avatar">{user.displayName.slice(0, 1).toUpperCase()}</span><span className="sidebar-account-copy"><b>{user.displayName}</b><small>{user.email}</small></span><i aria-hidden="true">⚙</i></summary>
          <div className="account-menu">
            <header><span>{user.displayName.slice(0, 1).toUpperCase()}</span><div><small>{user.role === "admin" ? "최고 관리자" : "현재 로그인 계정"}</small><b>{user.displayName}</b><p>{user.email}</p></div></header>
            <button type="button" className="account-dashboard-settings" onClick={() => { sidebarSettingsRef.current?.removeAttribute("open"); setShowSettings(true); }}><span>⚙</span><div><b>환경 설정</b><small>테마 · 조작 방식 · 소리 · 볼륨</small></div></button>
            {user.role === "admin" && <button type="button" className="account-admin-link" onClick={() => setWorkspaceView("admin")}><span>◆</span><div><b>관리자 페이지</b><small>회원 목록과 사용 기록 확인</small></div></button>}
            <a className="account-switch" href={authProvider === "password" ? "/api/auth/logout" : "/signout-with-chatgpt?return_to=%2F"}><span>⇄</span><div><b>계정 전환</b><small>로그아웃 후 다른 계정으로 로그인</small></div></a>
            <a className="account-signout" href={authProvider === "password" ? "/api/auth/logout" : "/signout-with-chatgpt?return_to=%2F"}><span>↗</span><b>로그아웃</b></a>
          </div>
        </details>
      </aside>
      <section className="dashboard-stage">
      {workspaceView === "admin" && user.role === "admin" ? <AdminWorkspace displayName={user.displayName} adminId={user.email} /> : workspaceView === "home" ? <section className="dashboard-home">
        <header className="home-hero"><div><p>TRAFFIC OPERATIONS</p><h1>{user.displayName}님, 오늘 조사도 정확하게.</h1><span>영상 준비부터 현장 카운팅과 기록 내보내기까지 한 화면에서 이어가세요.</span></div><div className="home-clock"><small>현재 기록 구간</small><b>{slots[Number(slot)].label}</b><span>{savedCurrent ? "저장 완료" : "작성 준비"}</span></div></header>
        <div className="home-stats"><article><span>저장된 시간대</span><b>{homeSavedSlots}<small>/ 96</small></b><i><em style={{ width: `${Math.round(homeSavedSlots / 96 * 100)}%` }} /></i></article><article><span>누적 통행량</span><b>{homeVehicleTotal.toLocaleString()}<small>대</small></b><p>{activeRecordSet.name}</p></article><article><span>클릭 기록</span><b>{activeRecordSet.clickLogs.length.toLocaleString()}<small>건</small></b><p>모든 +/− 조작 기록</p></article></div>
        <div className="home-grid"><section className="home-actions"><header><div><b>빠른 실행</b><span>작업을 선택해 바로 시작하세요.</span></div></header><button type="button" onClick={() => setWorkspaceView("ai")}><i>04</i><div><b>AI 차량 분석</b><span>로컬 GPU · 15분 유입/유출 자동 집계</span></div><strong>→</strong></button><button type="button" onClick={() => setWorkspaceView("live")}><i>03</i><div><b>실시간 영상 계수</b><span>영상 확인 · {activeRecordSet.name} · {slots[Number(slot)].label}</span></div><strong>→</strong></button><button type="button" onClick={() => setWorkspaceView("counter")}><i>02</i><div><b>차량 카운팅 이어하기</b><span>{activeRecordSet.name} · {slots[Number(slot)].label}</span></div><strong>→</strong></button><button type="button" onClick={() => setWorkspaceView("preprocess")}><i>01</i><div><b>새 영상 전처리</b><span>로컬 영상 선택 · 24시간 · 3배속</span></div><strong>→</strong></button></section><section className="home-activity"><header><div><b>최근 활동</b><span>현재 기록의 마지막 조작</span></div><button type="button" onClick={() => setWorkspaceView("counter")}>전체 보기</button></header><div>{homeRecentLogs.length ? homeRecentLogs.map((log, index) => <article key={`${log.t}-${index}`}><time>{formatClickTime(log.t).slice(11, 19)}</time><span className={log.d > 0 ? "plus" : "minus"}>{log.d > 0 ? "+1" : "−1"}</span><b>{log.n}번 · {movementName(log.m ?? movementForCounter(log.n))}</b><small>{log.b} → {log.a}</small></article>) : <p>아직 클릭 기록이 없습니다.<br />차량 카운팅을 시작하면 여기에 표시됩니다.</p>}</div></section></div>
      </section> : workspaceView === "preprocess" ? <PreprocessWorkspace onOpenCounter={() => setWorkspaceView("counter")} /> : workspaceView === "ai" ? <AiAnalysisWorkspace /> : workspaceView === "live" ? <LiveCountingWorkspace lanes={customPositions.map(({ id, movement, approach }) => ({ id, movement: movementName(movement), approach: approachName(approach) }))} counts={counts} total={total} slot={slot} slots={slots} recordId={activeRecordId} records={modeRecordSets.map((recordSet) => ({ id: recordSet.id, name: recordSet.name, saved: Object.keys(recordSet.records).length }))} savedCurrent={savedCurrent} onCount={changeCount} onSlotChange={selectSlot} onRecordChange={selectRecordSet} onSaveNext={saveAndNext} onOpenCounter={() => setWorkspaceView("counter")} /> : <div className="app-shell">
      <header className="topbar">
        <div><p className="eyebrow"><span className="live-dot" />현장 집계 중</p><h1>교차로 차량 카운터</h1></div>
        <div className="total-card" aria-live="polite"><span>현재 구간 합계</span><strong>{total.toLocaleString()}</strong><small>대</small></div>
      </header>

      <section className="record-toolbar" aria-label="기록 슬롯과 시간 선택">
        <div className="time-field record-set-field"><label htmlFor="record-set">기록 슬롯 · {modeName(mode)}</label><div className="record-set-controls"><select id="record-set" value={activeRecordId} onChange={(event) => selectRecordSet(event.target.value)}>{modeRecordSets.map((recordSet) => <option key={recordSet.id} value={recordSet.id}>{recordSet.name} · {Object.keys(recordSet.records).length}/96 저장</option>)}</select><button type="button" className="rename-record" onClick={startRenamingRecord}>이름 변경</button><button type="button" className="delete-record" onClick={requestDeleteRecord}>기록 삭제</button><button type="button" className="new-record" onClick={createRecordSet}>+ 새 기록</button></div>{editingRecordName && <form className="record-rename" onSubmit={(event) => { event.preventDefault(); saveRecordName(); }}><input autoFocus maxLength={40} value={recordNameDraft} onChange={(event) => setRecordNameDraft(event.target.value)} aria-label="기록 이름" /><button type="submit" disabled={!recordNameDraft.trim()}>저장</button><button type="button" onClick={() => setEditingRecordName(false)}>취소</button></form>}{confirmDeleteRecord && <div className="record-delete-confirm" role="alert"><span><b>{activeRecordSet.name}</b>과 저장된 모든 값을 삭제할까요? 복구할 수 없습니다.</span><button type="button" onClick={() => setConfirmDeleteRecord(false)}>취소</button><button type="button" className="confirm-delete" onClick={deleteRecordSet}>삭제</button></div>}</div>
        <div className="time-field wide"><label htmlFor="record-slot">기록 시간</label><select id="record-slot" value={slot} onChange={(e) => selectSlot(e.target.value)}>{slots.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select></div>
        <span className={`save-status ${savedCurrent ? "saved" : "draft"}`}>{savedCurrent ? "저장된 구간" : "작성 중"}</span>
        <button type="button" className="sheet-open" onClick={() => setShowSheet(true)}>저장 기록 보기</button>
      </section>

      <section className="custom-layout-toolbar"><div><b>{customConfig.name}</b><span>총 {customPositions.length}차선 · 저장 기록마다 다른 배치를 사용할 수 있습니다</span></div><button type="button" onClick={openCustomEditor}>교차로 구성</button></section>

      <section className="main-click-log" aria-label="최근 클릭 기록">
        <header><div><b>최근 클릭 로그</b><span>{activeRecordSet.clickLogs.length.toLocaleString()}건</span></div><button type="button" onClick={() => { setSheetView("clicks"); setShowSheet(true); }}>전체 로그</button></header>
        <div className="recent-click-list">{activeRecordSet.clickLogs.length ? [...activeRecordSet.clickLogs].slice(-8).reverse().map((log, index) => <div className={`recent-click ${log.d > 0 ? "plus" : "minus"}`} key={`${log.t}-${index}`}><time>{formatClickTime(log.t).slice(11, 19)}</time><b>{log.n}번 · {movementName(log.m ?? movementForCounter(log.n))}</b><strong>{log.d > 0 ? "+1" : "−1"}</strong><small>{log.b} → {log.a}</small></div>) : <p>카운터를 누르면 여기에 클릭 시각과 변경 내용이 표시됩니다.</p>}</div>
      </section>

      <section className="counter-panel custom-layout" aria-label="커스텀 교차로 차량 카운터">
        <div className="intersection" aria-hidden="true"><div className="road vertical-road" /><div className="road horizontal-road" /><div className="center-mark"><span>{slots[Number(slot)].label}</span><b>TOTAL</b><strong>{total}</strong></div></div>
        {(approachKeys as Approach[]).map((approach) => <div className={`custom-approach custom-${approach}`} key={approach} aria-label={`${approachName(approach)} ${customConfig.approaches[approach].length}차선`}><span className="custom-approach-label">{approachName(approach)} · {customConfig.approaches[approach].length}차선</span><div className="custom-lanes">{customConfig.approaches[approach].map((lane, index) => renderCounter(lane.id, `${approach}-${index}`, lane.movement))}</div></div>)}
      </section>

      <footer className="footer-bar">
        <p><span>●</span> 작성 중인 값도 이 기기에 자동 보관됩니다</p>
        <div className="footer-actions">
          {!confirmReset ? <button type="button" className="reset-button" onClick={() => setConfirmReset(true)}>현재 값 지우기</button> : <div className="reset-confirm"><span>현재 값을 지울까요?</span><button type="button" onClick={() => setConfirmReset(false)}>취소</button><button type="button" className="danger" onClick={resetDraft}>지우기</button></div>}
          <button type="button" className="save-next" onClick={saveAndNext}><span>{slots[Number(slot)].label} 저장</span><b>저장 후 다음 시간 →</b></button>
        </div>
      </footer>

      {showSheet && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && setShowSheet(false)}>
          <section className="sheet-modal" role="dialog" aria-modal="true" aria-label="저장 기록 표">
            <header><div><h2>{activeRecordSet.name} 저장 기록</h2><p>{sheetView === "summary" ? `${modeName(mode)} · ${Object.keys(records).length}/96 구간 저장 · 자정 이후에도 계속 이어집니다` : `${modeName(mode)} · 클릭 ${activeRecordSet.clickLogs.length.toLocaleString()}건 · 누른 실제 시각을 기록합니다`}</p></div><div className="sheet-actions"><button type="button" className="sheet-view-toggle" onClick={() => { setSheetView((current) => current === "summary" ? "clicks" : "summary"); setShowCorrection(false); setCopyState("표 복사"); }}>{sheetView === "summary" ? `클릭 로그 ${activeRecordSet.clickLogs.length.toLocaleString()}건` : "15분 집계"}</button>{sheetView === "summary" && <button type="button" className="time-correction-open" onClick={openCorrection}>시간 보정</button>}<button type="button" onClick={sheetView === "summary" ? copyTable : copyClickLogs}>{copyState}</button><button type="button" onClick={sheetView === "summary" ? downloadCsv : downloadClickLogs}>{sheetView === "summary" ? "CSV 다운로드" : "로그 CSV"}</button><button type="button" className="close-modal" onClick={() => setShowSheet(false)} aria-label="닫기">×</button></div></header>
            {sheetView === "summary" ? <>
            {showCorrection && <section className="time-correction" aria-label="저장 기록 시간 보정"><div className="time-correction-heading"><div><b>밀려 쓴 기록 옮기기</b><p>선택 범위에서 저장된 기록만 이동합니다. 작성 중인 값은 바뀌지 않습니다.</p></div>{correctionUndo && <button type="button" className="correction-undo" onClick={undoTimeCorrection}>방금 보정 되돌리기</button>}</div><div className="time-correction-controls"><label><span>시작 구간</span><select value={correctionStart} onChange={(event) => { setCorrectionStart(event.target.value); setCorrectionState({ kind: "idle", message: "" }); }}>{slots.map((item) => <option key={`correction-start-${item.key}`} value={item.key}>{item.label}</option>)}</select></label><label><span>끝 구간</span><select value={correctionEnd} onChange={(event) => { setCorrectionEnd(event.target.value); setCorrectionState({ kind: "idle", message: "" }); }}>{slots.map((item) => <option key={`correction-end-${item.key}`} value={item.key}>{item.label}</option>)}</select></label><label><span>이동 방향</span><select value={correctionOffset} onChange={(event) => { setCorrectionOffset(Number(event.target.value) as -1 | 1); setCorrectionState({ kind: "idle", message: "" }); }}><option value={-1}>15분 앞으로 · 18:15 → 18:00</option><option value={1}>15분 뒤로 · 18:00 → 18:15</option></select></label><button type="button" className="correction-apply" onClick={applyTimeCorrection}>보정 적용</button></div>{correctionState.message && <p className={`correction-message ${correctionState.kind}`} role="status">{correctionState.message}</p>}<small>이동할 시간대에 다른 기록이 있으면 덮어쓰지 않고 중단합니다. 저장된 0값 구간은 교체할 수 있습니다.</small></section>}
            {!isGyuhoMode && !isCustomMode ? <div className="excel-import">
              <div><b>엑셀 자동 입력</b><p>{isTwoWayMode ? "저장된 15분 기록을 시간별로 합산해 동두천보건소 양식의 유입·유출 칸에 넣습니다. 기록이 없는 시간과 원본 서식은 그대로 유지됩니다." : "저장된 번호별 차량 수를 같은 15분 시간대의 소계 칸에 넣습니다. 원본 서식과 다른 값은 그대로 유지됩니다."}</p></div>
              <label className="excel-file"><input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => { setExcelFile(event.target.files?.[0] ?? null); setExcelState({ kind: "idle", message: "" }); }} /><span>{excelFile ? excelFile.name : "엑셀 파일 선택"}</span></label>
              <button type="button" className="excel-fill" disabled={excelState.kind === "working"} onClick={fillExcelTemplate}>{excelState.kind === "working" ? "입력 중…" : "기록 입력 후 다운로드"}</button>
              {excelState.message && <p className={`excel-message ${excelState.kind}`} role="status">{excelState.message}</p>}
            </div> : <div className="gyuho-export-note"><b>{isCustomMode ? "커스텀 기록 내보내기" : "차량 분류 기록 내보내기"}</b><p>{isCustomMode ? "설정한 번호와 방향은 표 복사 또는 CSV 다운로드로 내보냅니다." : "규호 모드는 방향별 6개 차량 분류를 표 복사 또는 CSV 다운로드로 내보냅니다."}</p></div>}
            <div className="table-wrap"><table><thead><tr><th>시간</th>{tableColumns.map(({ key, label }) => <th key={key}>{label}</th>)}<th>합계</th></tr></thead><tbody>{slots.map(({ key: rowSlot, label }) => { const row = records[rowSlot]; const values = tableColumns.map(({ key }) => row?.[key] ?? 0); const sum = values.reduce((a, b) => a + b, 0); return <tr className={row ? "has-data" : ""} key={rowSlot}><th>{label}</th>{values.map((value, index) => <td key={tableColumns[index].key}>{value}</td>)}<td className="row-total">{sum}</td></tr>; })}</tbody></table></div>
            </> : <section className="click-log-panel" aria-label="클릭 기록 로그">
              <div className="click-log-summary"><div><b>{activeRecordSet.clickLogs.length.toLocaleString()}건</b><span>모든 +/− 조작을 실제 클릭 시각 순서로 저장합니다.</span></div><small>최신 기록이 위에 표시됩니다.</small></div>
              <div className="table-wrap click-log-wrap"><table className="click-log-table"><thead><tr><th>클릭 시각</th><th>기록 시간</th><th>모드</th><th>기록명</th><th>번호</th><th>방향/차량</th><th>조작</th><th>변경 전</th><th>변경 후</th></tr></thead><tbody>{clickLogRows().length ? [...clickLogRows()].reverse().map((row, index) => <tr key={`${row[0]}-${index}`}>{row.map((value, column) => <td className={column === 6 ? value === "+1" ? "log-plus" : "log-minus" : undefined} key={column}>{value}</td>)}</tr>) : <tr><td className="empty-click-log" colSpan={9}>아직 기록된 클릭이 없습니다.</td></tr>}</tbody></table></div>
            </section>}
          </section>
        </div>
      )}

      {showCustomSettings && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setShowCustomSettings(false)}>
          <section className="custom-config-modal" role="dialog" aria-modal="true" aria-label="커스텀 교차로 구성">
            <header><div><h2>커스텀 교차로 구성</h2><p>이 설정은 현재 선택한 저장 기록에만 적용됩니다</p></div><button type="button" className="settings-close" onClick={() => setShowCustomSettings(false)} aria-label="닫기">×</button></header>
            <div className="custom-config-body">
              <section className="custom-presets"><b>배치 예시 불러오기</b><div><button type="button" onClick={() => loadCustomPreset("full")}>12차선 기본</button><button type="button" onClick={() => loadCustomPreset("photo")}>6차선</button><button type="button" onClick={() => loadCustomPreset("box")}>12차선 반대</button><button type="button" onClick={() => loadCustomPreset("gyuho")}>12차선 교차</button><button type="button" onClick={() => loadCustomPreset("blank")}>빈 교차로</button></div></section>
              <label className="custom-name-field"><span>교차로 이름</span><input maxLength={40} value={customDraft.name} onChange={(event) => setCustomDraft((current) => ({ ...current, name: event.target.value }))} /></label>
              <div className="approach-settings">{(approachKeys as Approach[]).map((approach) => <section className="approach-editor" key={approach}><header><b>{approachName(approach)}</b><label><span>차선 수</span><select value={customDraft.approaches[approach].length} onChange={(event) => changeApproachCount(approach, Number(event.target.value))}>{Array.from({ length: 7 }, (_, count) => <option value={count} key={count}>{count}개</option>)}</select></label></header><div className="lane-editors">{customDraft.approaches[approach].map((lane, index) => <div className="lane-editor" key={`${approach}-${index}`}><span>{index + 1}차선</span><label><small>번호</small><input type="number" min="1" max="99" value={lane.id} onChange={(event) => updateCustomLane(approach, index, { id: Number(event.target.value) })} aria-label={`${approachName(approach)} ${index + 1}차선 번호`} /></label><label><small>방향</small><select value={lane.movement} onChange={(event) => updateCustomLane(approach, index, { movement: event.target.value as Movement })} aria-label={`${approachName(approach)} ${index + 1}차선 방향`}><option value="left">좌회전</option><option value="straight">직진</option><option value="right">우회전</option></select></label></div>)}</div></section>)}</div>
              <p className="custom-config-note">번호는 교차로 전체에서 중복 없이 사용하세요. 이미 저장된 값은 번호를 기준으로 유지되므로 기록 도중 번호를 바꿀 때 주의하세요.</p>
              {customConfigError && <p className="custom-config-error" role="alert">{customConfigError}</p>}
            </div>
            <footer><button type="button" className="custom-cancel" onClick={() => setShowCustomSettings(false)}>취소</button><button type="button" className="custom-save" onClick={saveCustomConfig}>이 구성 사용</button></footer>
          </section>
        </div>
      )}
      </div>}

      {showSettings && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && setShowSettings(false)}>
          <section className="settings-modal" role="dialog" aria-modal="true" aria-label="환경 설정">
            <header><div><h2>환경 설정</h2><p>모든 페이지의 화면과 차량 카운팅 조작·소리를 설정합니다</p></div><button type="button" className="settings-close" onClick={() => setShowSettings(false)} aria-label="닫기">×</button></header>
            <div className="settings-body">
              <fieldset><legend>테마 색</legend><div className="theme-options">
                <button type="button" className={theme === "light" ? "selected" : ""} onClick={() => setTheme("light")}><i className="swatch light" /><span><b>흰색</b><small>밝고 선명하게</small></span></button>
                <button type="button" className={theme === "dark" ? "selected" : ""} onClick={() => setTheme("dark")}><i className="swatch dark" /><span><b>검정색</b><small>어두운 환경</small></span></button>
                <button type="button" className={theme === "green" ? "selected" : ""} onClick={() => setTheme("green")}><i className="swatch green" /><span><b>은은한 그린</b><small>눈이 편안한 색감</small></span></button>
              </div></fieldset>
              <fieldset><legend>카운터 조작 방식</legend><div className="input-style-options"><button type="button" className={inputStyle === "card" ? "selected" : ""} onClick={() => setInputStyle("card")}><b>카드 클릭</b><small>좌클릭 +1 · 우클릭 −1</small></button><button type="button" className={inputStyle === "buttons" ? "selected" : ""} onClick={() => setInputStyle("buttons")}><b>− / + 버튼</b><small>모바일에서 편리한 방식</small></button></div><p className="input-style-note">교차로의 모든 번호에 동일하게 적용됩니다.</p></fieldset>
              <fieldset><legend>버튼 소리</legend><label className="sound-toggle"><span><b>소리 사용</b><small>− / + 버튼을 누를 때 재생</small></span><input type="checkbox" checked={soundOn} onChange={(e) => setSoundOn(e.target.checked)} /><i /></label>
                <div className="sound-options">{soundNames.map((name) => <button type="button" key={name} disabled={!soundOn} className={soundName === name ? "selected" : ""} onClick={() => setSoundName(name)}>{soundLabel(name)}</button>)}</div>
                <label className={`volume-control ${!soundOn ? "disabled" : ""}`}><span><b>볼륨</b><output>{volume}%</output></span><input type="range" min="0" max="100" step="5" value={volume} disabled={!soundOn} onChange={(e) => setVolume(Number(e.target.value))} aria-label="버튼 소리 볼륨" /></label>
                <button type="button" className="sound-preview" disabled={!soundOn} onClick={() => playSound(true)}>소리 미리 듣기</button>
                <div className={`counter-sound-settings ${!soundOn ? "disabled" : ""}`}><div className="counter-sound-heading"><b>번호별 소리</b><small>기본 소리와 다르게 들릴 번호만 변경하세요</small></div><div className="counter-sound-grid">{soundConfigIds.map((id) => <label key={`custom-${id}`}><b>{id}번</b><select disabled={!soundOn} value={counterSounds.custom[id] ?? "default"} onChange={(event) => { const nextSound = event.target.value as CounterSound; setCounterSounds((current) => ({ ...current, custom: { ...current.custom, [id]: nextSound } })); playSound(true, 1, nextSound === "default" ? soundName : nextSound); }} aria-label={`${id}번 소리`}><option value="default">기본 · {soundLabel(soundName)}</option>{soundNames.map((name) => <option key={name} value={name}>{soundLabel(name)}</option>)}</select></label>)}</div></div>
              </fieldset>
            </div>
            <footer><button type="button" onClick={() => setShowSettings(false)}>완료</button></footer>
          </section>
        </div>
      )}
      </section>
    </main>
  );
}
