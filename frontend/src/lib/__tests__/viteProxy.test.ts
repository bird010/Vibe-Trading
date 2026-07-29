import { readFileSync } from "node:fs";
import { resolve } from "node:path";


describe("Vite API proxy", () => {
  it("proxies the dedicated StockPred API surface", () => {
    const configPath = resolve(process.cwd(), "vite.config.ts");
    const source = readFileSync(configPath, "utf8");

    expect(source).toContain('"/stockpred": apiProxyWithHtmlFallback');
  });
});
