import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

// 배포 환경에서 사용하는 회원 및 사용 이벤트 D1 테이블 정의다.

export const members = sqliteTable("members", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  email: text("email").notNull(),
  provider: text("provider").notNull(),
  role: text("role").notNull().default("user"),
  createdAt: integer("created_at").notNull(),
  lastSeenAt: integer("last_seen_at").notNull(),
  loginCount: integer("login_count").notNull().default(0),
});

export const usageEvents = sqliteTable("usage_events", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  memberId: text("member_id").notNull(),
  memberName: text("member_name").notNull(),
  memberEmail: text("member_email").notNull(),
  eventType: text("event_type").notNull(),
  detail: text("detail").notNull(),
  createdAt: integer("created_at").notNull(),
}, (table) => [
  index("idx_usage_events_created_at").on(table.createdAt),
  index("idx_usage_events_member_id_created_at").on(table.memberId, table.createdAt),
]);
