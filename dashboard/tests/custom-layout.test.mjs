import assert from "node:assert/strict";
// 사용자 정의 교차로 배치의 정규화와 차로 크기 변경을 검증한다.
import test from "node:test";
import { customPreset, nextLaneNumber, normalizeCustomConfig, resizeApproach } from "../app/custom-layout.mjs";

test("기존 모드 배치를 커스텀 프리셋으로 불러온다", () => {
  const config = customPreset("box");
  assert.deepEqual(config.approaches.north.map(({ id }) => id), [3, 2, 1]);
  assert.deepEqual(config.approaches.east.map(({ id }) => id), [12, 11, 10]);
  assert.equal(nextLaneNumber(config), 13);
});

test("방향별 차선 수를 0개부터 6개까지 조정한다", () => {
  const config = customPreset("blank");
  const resized = resizeApproach(config, "north", 4);
  assert.equal(resized.approaches.north.length, 4);
  assert.deepEqual(resized.approaches.north.map(({ id }) => id), [1, 2, 3, 4]);
  assert.equal(resizeApproach(resized, "north", 9).approaches.north.length, 6);
});

test("중복 번호와 잘못된 방향을 정리한다", () => {
  const config = normalizeCustomConfig({
    name: " 테스트 교차로 ",
    approaches: {
      north: [{ id: 1, movement: "left" }, { id: 1, movement: "right" }],
      east: [{ id: 2, movement: "unknown" }], south: [], west: [],
    },
  });
  assert.equal(config.name, "테스트 교차로");
  assert.deepEqual(config.approaches.north, [{ id: 1, movement: "left" }]);
  assert.deepEqual(config.approaches.east, [{ id: 2, movement: "straight" }]);
});
