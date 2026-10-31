import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  { ignores: [".next/**", "node_modules/**", "src/lib/api/schema.d.ts", "playwright-report/**", "test-results/**"] },
];

export default config;
