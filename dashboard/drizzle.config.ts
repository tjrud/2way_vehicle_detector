// D1 데이터베이스 마이그레이션을 생성하는 Drizzle Kit 설정이다.
import { defineConfig } from "drizzle-kit";

export default defineConfig({
  out: "./drizzle",
  schema: "./db/schema.ts",
  dialect: "sqlite",
});
