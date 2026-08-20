import assert from "node:assert/strict";
// 2way 15분 기록이 시간별 엑셀 값으로 정확히 합산되는지 검증한다.
import test from "node:test";
import { summarizeTwoWayHours } from "../app/excel-mapping.mjs";

test("2way의 15분 기록을 시간별 유입·유출로 합산한다", () => {
  const summary = summarizeTwoWayHours({
    "24": { 1: 1, 2: 2 },
    "25": { 1: 3, 2: 4 },
    "26": { 1: 5, 2: 6 },
    "27": { 1: 7, 2: 8 },
    "28": { 1: 9, 2: 10 },
  });
  assert.deepEqual(summary[6], { incoming: 16, outgoing: 20, savedSlots: 4 });
  assert.deepEqual(summary[7], { incoming: 9, outgoing: 10, savedSlots: 1 });
});

test("기록이 없는 시간은 엑셀 입력 대상에서 제외한다", () => {
  const summary = summarizeTwoWayHours({ "35": { 1: 0, 2: 0 } });
  assert.deepEqual(summary[8], { incoming: 0, outgoing: 0, savedSlots: 1 });
  assert.equal(summary[9], undefined);
});
