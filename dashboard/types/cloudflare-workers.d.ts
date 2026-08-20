// 로컬 TypeScript 검사에서도 Cloudflare 런타임 env 타입을 사용할 수 있게 선언한다.
declare module "cloudflare:workers" {
  export const env: Record<string, unknown>;
}
