// 사용자 정의 교차로의 접근 방향과 차로 배치를 검증·크기 조정하는 순수 함수 모음이다.
export const approachKeys = ["north", "east", "south", "west"];
export const movementKeys = ["left", "straight", "right"];

const lane = (id, movement) => ({ id, movement });

export const customPresets = {
  full: {
    name: "12차선 기본 교차로",
    approaches: {
      north: [lane(9, "right"), lane(8, "straight"), lane(7, "left")],
      east: [lane(6, "right"), lane(5, "straight"), lane(4, "left")],
      south: [lane(1, "left"), lane(2, "straight"), lane(3, "right")],
      west: [lane(10, "left"), lane(11, "straight"), lane(12, "right")],
    },
  },
  photo: {
    name: "6차선 교차로",
    approaches: {
      north: [lane(3, "right"), lane(2, "straight")],
      east: [],
      south: [lane(7, "left"), lane(8, "straight")],
      west: [lane(4, "left"), lane(6, "right")],
    },
  },
  box: {
    name: "모드 3 교차로",
    approaches: {
      north: [lane(3, "right"), lane(2, "straight"), lane(1, "left")],
      east: [lane(12, "right"), lane(11, "straight"), lane(10, "left")],
      south: [lane(7, "left"), lane(8, "straight"), lane(9, "right")],
      west: [lane(4, "left"), lane(5, "straight"), lane(6, "right")],
    },
  },
  gyuho: {
    name: "규호 교차로",
    approaches: {
      north: [lane(6, "right"), lane(5, "straight"), lane(4, "left")],
      east: [lane(3, "right"), lane(2, "straight"), lane(1, "left")],
      south: [lane(10, "left"), lane(11, "straight"), lane(12, "right")],
      west: [lane(7, "left"), lane(8, "straight"), lane(9, "right")],
    },
  },
  blank: {
    name: "새 교차로",
    approaches: { north: [], east: [], south: [], west: [] },
  },
};

const cloneConfig = (config) => ({
  name: config.name,
  approaches: Object.fromEntries(approachKeys.map((key) => [key, config.approaches[key].map((item) => ({ ...item }))])),
});

export const defaultCustomConfig = () => cloneConfig(customPresets.full);
export const customPreset = (name) => cloneConfig(customPresets[name] ?? customPresets.full);

export const normalizeCustomConfig = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return defaultCustomConfig();
  const used = new Set();
  const approaches = {};
  for (const key of approachKeys) {
    const source = Array.isArray(value.approaches?.[key]) ? value.approaches[key].slice(0, 6) : [];
    approaches[key] = source.flatMap((item) => {
      const id = Number(item?.id);
      if (!Number.isInteger(id) || id < 1 || id > 99 || used.has(id)) return [];
      used.add(id);
      return [{ id, movement: movementKeys.includes(item?.movement) ? item.movement : "straight" }];
    });
  }
  if (used.size === 0) return defaultCustomConfig();
  return {
    name: typeof value.name === "string" && value.name.trim() ? value.name.trim().slice(0, 40) : "커스텀 교차로",
    approaches,
  };
};

export const nextLaneNumber = (config) => {
  const used = new Set(approachKeys.flatMap((key) => config.approaches[key].map((item) => item.id)));
  for (let id = 1; id <= 99; id += 1) if (!used.has(id)) return id;
  return 99;
};

export const resizeApproach = (config, approach, count) => {
  const size = Math.max(0, Math.min(6, Math.trunc(Number(count) || 0)));
  const next = cloneConfig(config);
  while (next.approaches[approach].length < size) {
    next.approaches[approach].push(lane(nextLaneNumber(next), "straight"));
  }
  next.approaches[approach] = next.approaches[approach].slice(0, size);
  return next;
};
