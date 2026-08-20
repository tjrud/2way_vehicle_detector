// 96개 15분 슬롯의 2way 기록을 엑셀 템플릿이 요구하는 시간별 값으로 합친다.
export const summarizeTwoWayHours = (records) => {
  const hours = {};
  if (!records || typeof records !== "object" || Array.isArray(records)) return hours;

  Object.entries(records).forEach(([slot, counts]) => {
    const slotNumber = Number(slot);
    if (!Number.isInteger(slotNumber) || slotNumber < 0 || slotNumber >= 96 || !counts || typeof counts !== "object") return;
    const hour = Math.floor(slotNumber / 4);
    const current = hours[hour] ?? { incoming: 0, outgoing: 0, savedSlots: 0 };
    current.incoming += Number.isFinite(Number(counts[1])) ? Math.max(0, Math.trunc(Number(counts[1]))) : 0;
    current.outgoing += Number.isFinite(Number(counts[2])) ? Math.max(0, Math.trunc(Number(counts[2]))) : 0;
    current.savedSlots += 1;
    hours[hour] = current;
  });

  return hours;
};
