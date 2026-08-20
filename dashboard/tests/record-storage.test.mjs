import assert from "node:assert/strict";
// 이전 버전 기록의 마이그레이션과 시간 이동 시 데이터 보존을 검증한다.
import test from "node:test";
import { importedTwoWayRecords, migrateDrafts, migrateRecords, migrateToLibrary, normalizeClickLogs, shiftRecords } from "../app/record-storage.mjs";

const counts = (value) => Object.fromEntries(Array.from({ length: 12 }, (_, index) => [index + 1, value]));

test("클릭 로그의 시간대와 증감 값을 복구한다", () => {
  const logs = normalizeClickLogs([
    { t: 1722844800123, s: "48", n: 3, m: "straight", d: 1, b: 7, a: 8 },
    { t: 1722844801123, s: "49", n: 3, d: -1, b: 8, a: 7 },
    { t: "잘못된 값", s: "99", n: 0, d: 4, b: 0, a: 0 },
  ]);
  assert.equal(logs.length, 2);
  assert.equal(logs[0].s, "48");
  assert.equal(logs[0].m, "straight");
  assert.equal(logs[1].d, -1);
  assert.equal(logs[1].a, 7);
});

test("19시부터 자정까지의 전날 기록과 00시 이후 기록을 합친다", () => {
  const previous = {};
  for (let slot = 76; slot < 96; slot += 1) previous[String(slot).padStart(2, "0")] = counts(slot);
  const current = {};
  for (let slot = 0; slot <= 28; slot += 1) current[String(slot).padStart(2, "0")] = counts(slot);

  const migrated = migrateRecords({ "2026-08-01": previous, "2026-08-02": current });
  assert.equal(Object.keys(migrated).length, 49);
  assert.equal(migrated["76"][1], 76);
  assert.equal(migrated["95"][12], 95);
  assert.equal(migrated["00"][1], 0);
  assert.equal(migrated["28"][12], 28);
});

test("날짜가 포함된 작성 중 키도 시간 키로 복구한다", () => {
  const migrated = migrateDrafts({ "2026-08-01|95": counts(95), "2026-08-02|00": counts(0) });
  assert.deepEqual(Object.keys(migrated), ["95", "00"]);
});

test("기존 하루 기록을 사용 중이던 모드의 기록 1로 이전한다", () => {
  const migrated = migrateToLibrary({ mode: "photo", records: { "12": counts(12) }, drafts: {}, slot: "13" });
  assert.equal(migrated.library.photo[0].records["12"][1], 12);
  assert.equal(migrated.library.photo[0].slot, "13");
  assert.deepEqual(migrated.library.full[0].records, {});
});

test("12개 모드와 6개 모드의 여러 기록 슬롯을 분리해서 유지한다", () => {
  const migrated = migrateToLibrary({
    library: {
      full: [
        { id: "full-a", name: "기록 1", records: { "00": counts(1) }, drafts: {}, slot: "01" },
        { id: "full-b", name: "기록 2", records: { "00": counts(2) }, drafts: {}, slot: "02" },
      ],
      photo: [{ id: "photo-a", name: "기록 1", records: { "00": counts(3) }, drafts: {}, slot: "03" }],
    },
    activeRecordIds: { full: "full-b", photo: "photo-a" },
  });
  assert.equal(migrated.library.full[1].records["00"][1], 2);
  assert.equal(migrated.library.photo[0].records["00"][1], 3);
  assert.equal(migrated.activeRecordIds.full, "full-b");
  assert.equal(migrated.library.box[0].id, "box-1");
  assert.equal(migrated.library.gyuho[0].id, "gyuho-1");
  assert.equal(migrated.library.twoway[0].id, "twoway-1");
  assert.equal(migrated.library.custom[0].id, "custom-1");
});

test("2way 모드는 유입·유출 기록을 다른 모드와 분리한다", () => {
  const migrated = migrateToLibrary({
    mode: "twoway",
    records: { "24": { 1: 17, 2: 12 } },
    drafts: {},
    slot: "25",
  });
  assert.equal(migrated.library.twoway[0].records["24"][1], 17);
  assert.equal(migrated.library.twoway[0].records["24"][2], 12);
  assert.deepEqual(migrated.library.full[0].records, {});
});

test("CSV의 값이 있는 18개 구간을 2way 기록 1에 한 번만 반영한다", () => {
  const migrated = migrateToLibrary({
    dataVersion: 3,
    library: {
      full: [], photo: [], box: [], gyuho: [],
      twoway: [{ id: "twoway-1", name: "기록 1", records: { "25": { 1: 9, 2: 9 } }, drafts: {}, slot: "26" }],
    },
    activeRecordIds: { twoway: "twoway-1" },
  });
  const records = migrated.library.twoway[0].records;
  assert.equal(Object.keys(importedTwoWayRecords).length, 18);
  assert.equal(Object.values(importedTwoWayRecords).reduce((sum, row) => sum + row[1], 0), 21);
  assert.equal(Object.values(importedTwoWayRecords).reduce((sum, row) => sum + row[2], 0), 46);
  assert.deepEqual(records["25"], { 1: 9, 2: 9 });
  assert.deepEqual(records["35"], { 1: 8, 2: 4 });
  assert.equal(migrated.library.twoway[0].slot, "26");
});

test("규호 모드의 방향별 차량 분류 기록을 별도로 저장한다", () => {
  const classified = { ...counts(0), "1:passenger": 7, "1:busSmall": 2, "12:trailer": 1 };
  const migrated = migrateToLibrary({
    mode: "gyuho",
    records: { "00": classified },
    drafts: { "01": { ...classified, "1:passenger": 8 } },
    slot: "01",
  });
  assert.equal(migrated.library.gyuho[0].records["00"]["1:passenger"], 7);
  assert.equal(migrated.library.gyuho[0].records["00"]["12:trailer"], 1);
  assert.equal(migrated.library.gyuho[0].drafts["01"]["1:passenger"], 8);
  assert.deepEqual(migrated.library.full[0].records, {});
});

test("모드 3 기록도 다른 모드와 별도 저장한다", () => {
  const migrated = migrateToLibrary({
    mode: "box",
    records: { "00": counts(30) },
    drafts: {},
    slot: "01",
  });
  assert.equal(migrated.library.box[0].records["00"][3], 30);
  assert.deepEqual(migrated.library.full[0].records, {});
  assert.deepEqual(migrated.library.photo[0].records, {});
});

test("선택한 저장 기록을 15분 앞으로 보정한다", () => {
  const original = { "73": counts(73), "75": counts(75) };
  const shifted = shiftRecords(original, "73", "75", -1);
  assert.equal(shifted.moved, 2);
  assert.equal(shifted.records["72"][1], 73);
  assert.equal(shifted.records["74"][12], 75);
  assert.equal(shifted.records["73"], undefined);
  assert.equal(shifted.records["75"], undefined);
});

test("자정을 넘는 범위도 15분 뒤로 보정한다", () => {
  const shifted = shiftRecords({ "95": counts(95), "00": counts(0) }, "95", "00", 1);
  assert.equal(shifted.records["00"][1], 95);
  assert.equal(shifted.records["01"][12], 0);
});

test("이동할 시간대에 다른 기록이 있으면 덮어쓰지 않는다", () => {
  const original = { "72": counts(72), "73": counts(73) };
  assert.throws(() => shiftRecords(original, "73", "73", -1), /다른 기록/);
  assert.equal(original["72"][1], 72);
  assert.equal(original["73"][1], 73);
});

test("대상 시간대가 저장된 0이면 보정 기록으로 교체할 수 있다", () => {
  const shifted = shiftRecords({ "72": counts(0), "73": counts(73) }, "73", "73", -1);
  assert.equal(shifted.records["72"][1], 73);
  assert.equal(shifted.records["73"], undefined);
});
