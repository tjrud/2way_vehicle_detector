// 수동 집계 기록을 버전이 있는 브라우저 저장 형식으로 정규화·이전한다.
const pad = (value) => String(value).padStart(2, "0");

const isCounts = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const entries = Object.entries(value);
  return entries.length > 0 && entries.every(([id, count]) => /^([1-9]|1[0-2])(?::(passenger|busSmall|busLarge|truckSmall|truckLarge|trailer))?$/.test(id) && typeof count === "number");
};

const slotKey = (value) => {
  const rawSlot = value.includes("|") ? value.split("|").at(-1) : value;
  const number = Number(rawSlot);
  return Number.isInteger(number) && number >= 0 && number < 96 ? pad(number) : null;
};

export const migrateRecords = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const entries = Object.entries(value);
  if (entries.every(([key, counts]) => slotKey(key) !== null && isCounts(counts))) {
    return Object.fromEntries(entries.map(([key, counts]) => [slotKey(key), counts]));
  }

  const datedEntries = entries
    .filter(([, day]) => day && typeof day === "object" && !Array.isArray(day))
    .sort(([left], [right]) => left.localeCompare(right));
  if (!datedEntries.length) return {};
  const [, latestDay] = datedEntries.at(-1);
  const result = {};
  const previousEntry = datedEntries.at(-2);

  if (previousEntry && isCounts(previousEntry[1]["95"]) && isCounts(latestDay["00"])) {
    const previousDay = previousEntry[1];
    let firstOvernightSlot = 95;
    while (firstOvernightSlot > 0 && isCounts(previousDay[pad(firstOvernightSlot - 1)])) firstOvernightSlot -= 1;
    for (let index = firstOvernightSlot; index < 96; index += 1) {
      const counts = previousDay[pad(index)];
      if (isCounts(counts)) result[pad(index)] = counts;
    }
  }

  Object.entries(latestDay).forEach(([key, counts]) => {
    const normalizedSlot = slotKey(key);
    if (normalizedSlot && isCounts(counts)) result[normalizedSlot] = counts;
  });
  return result;
};

export const migrateDrafts = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const result = {};
  Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).forEach(([key, counts]) => {
    const normalizedSlot = slotKey(key);
    if (normalizedSlot && isCounts(counts)) result[normalizedSlot] = counts;
  });
  return result;
};

const rangeSlots = (start, end) => {
  const result = [];
  let current = Number(start);
  const last = Number(end);
  for (let count = 0; count < 96; count += 1) {
    result.push(pad(current));
    if (current === last) break;
    current = (current + 1) % 96;
  }
  return result;
};

const hasNonZeroCount = (counts) => Object.values(counts ?? {}).some((count) => Number(count) !== 0);

export const shiftRecords = (records, start, end, offset) => {
  const normalizedStart = slotKey(String(start));
  const normalizedEnd = slotKey(String(end));
  if (!normalizedStart || !normalizedEnd || ![-1, 1].includes(offset)) throw new Error("보정 범위가 올바르지 않습니다.");

  const selectedSlots = rangeSlots(normalizedStart, normalizedEnd);
  const sourceSlots = selectedSlots.filter((slot) => isCounts(records?.[slot]));
  if (!sourceSlots.length) throw new Error("선택한 범위에 저장된 기록이 없습니다.");

  const sourceSet = new Set(sourceSlots);
  const moves = sourceSlots.map((source) => ({
    source,
    target: pad((Number(source) + offset + 96) % 96),
    counts: records[source],
  }));
  const collision = moves.find(({ target }) => !sourceSet.has(target) && isCounts(records[target]) && hasNonZeroCount(records[target]));
  if (collision) throw new Error(`${collision.target} 시간대에 다른 기록이 있어 이동할 수 없습니다.`);

  const shifted = { ...records };
  sourceSlots.forEach((source) => delete shifted[source]);
  moves.forEach(({ target, counts }) => { shifted[target] = counts; });
  return { records: shifted, moved: moves.length };
};

const normalizeSlot = (value, fallback = "00") => {
  const normalized = slotKey(String(value ?? ""));
  return normalized ?? fallback;
};

export const normalizeClickLogs = (value) => {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => item && typeof item === "object")
    .map((item) => ({
      t: Number(item.t), s: slotKey(String(item.s ?? "")), n: Number(item.n),
      ...(typeof item.v === "string" ? { v: item.v } : {}),
      ...(typeof item.m === "string" ? { m: item.m } : {}),
      d: Number(item.d), b: Number(item.b), a: Number(item.a),
    }))
    .filter((item) => Number.isFinite(item.t) && item.s && Number.isInteger(item.n)
      && item.n >= 1 && item.n <= 99 && [-1, 1].includes(item.d)
      && Number.isFinite(item.b) && Number.isFinite(item.a));
};

const emptySet = (mode, index = 1) => ({
  id: `${mode}-${index}`,
  name: `기록 ${index}`,
  records: {},
  drafts: {},
  slot: "00",
  clickLogs: [],
});

export const importedTwoWayRecords = {
  "25": { 1: 1, 2: 3 }, "26": { 1: 0, 2: 1 }, "27": { 1: 2, 2: 1 }, "28": { 1: 1, 2: 3 },
  "32": { 1: 1, 2: 1 }, "33": { 1: 3, 2: 2 }, "34": { 1: 4, 2: 5 }, "35": { 1: 8, 2: 4 },
  "36": { 1: 0, 2: 6 }, "37": { 1: 0, 2: 7 }, "38": { 1: 0, 2: 2 }, "39": { 1: 0, 2: 3 },
  "40": { 1: 1, 2: 0 }, "41": { 1: 0, 2: 3 }, "42": { 1: 0, 2: 1 }, "44": { 1: 0, 2: 1 },
  "45": { 1: 0, 2: 1 }, "47": { 1: 0, 2: 2 },
};

const cloneImportedTwoWayRecords = () => Object.fromEntries(
  Object.entries(importedTwoWayRecords).map(([slot, counts]) => [slot, { ...counts }]),
);

const importedTwoWaySet = () => ({
  ...emptySet("twoway"),
  records: cloneImportedTwoWayRecords(),
  slot: "48",
});

export const createEmptyLibrary = () => ({
  library: { full: [emptySet("full")], photo: [emptySet("photo")], box: [emptySet("box")], gyuho: [emptySet("gyuho")], twoway: [importedTwoWaySet()], custom: [emptySet("custom")] },
  activeRecordIds: { full: "full-1", photo: "photo-1", box: "box-1", gyuho: "gyuho-1", twoway: "twoway-1", custom: "custom-1" },
});

const normalizeSet = (value, mode, index) => {
  const fallback = emptySet(mode, index + 1);
  if (!value || typeof value !== "object" || Array.isArray(value)) return fallback;
  return {
    id: typeof value.id === "string" && value.id ? value.id : fallback.id,
    name: typeof value.name === "string" && value.name.trim() ? value.name.trim() : fallback.name,
    records: migrateRecords(value.records),
    drafts: migrateDrafts(value.drafts),
    slot: normalizeSlot(value.slot),
    clickLogs: normalizeClickLogs(value.clickLogs),
    ...(mode === "custom" && value.customConfig && typeof value.customConfig === "object" ? { customConfig: value.customConfig } : {}),
  };
};

export const migrateToLibrary = (value) => {
  const defaults = createEmptyLibrary();
  if (!value || typeof value !== "object" || Array.isArray(value)) return defaults;

  if (value.library && typeof value.library === "object") {
    for (const mode of ["full", "photo", "box", "gyuho", "twoway", "custom"]) {
      const sourceSets = Array.isArray(value.library[mode]) ? value.library[mode] : [];
      const sets = sourceSets.map((set, index) => normalizeSet(set, mode, index));
      defaults.library[mode] = sets.length ? sets : [emptySet(mode)];
      const requestedId = value.activeRecordIds?.[mode];
      defaults.activeRecordIds[mode] = defaults.library[mode].some((set) => set.id === requestedId)
        ? requestedId
        : defaults.library[mode][0].id;
    }
    if (value.dataVersion !== 4) {
      const [first, ...rest] = defaults.library.twoway;
      const hasExistingRecords = Object.keys(first.records).length > 0;
      defaults.library.twoway = [{
        ...first,
        records: { ...cloneImportedTwoWayRecords(), ...first.records },
        slot: hasExistingRecords ? first.slot : "48",
      }, ...rest];
    }
    return defaults;
  }

  const mode = value.mode === "photo" ? "photo" : value.mode === "box" ? "box" : value.mode === "gyuho" ? "gyuho" : value.mode === "twoway" ? "twoway" : value.mode === "custom" ? "custom" : "full";
  defaults.library[mode][0] = {
    ...defaults.library[mode][0],
    records: migrateRecords(value.records),
    drafts: migrateDrafts(value.drafts),
    slot: normalizeSlot(value.slot),
  };
  return defaults;
};
