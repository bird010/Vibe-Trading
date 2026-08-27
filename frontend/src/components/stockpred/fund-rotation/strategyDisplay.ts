const AI_ROTATION_ID = /^ai_rotation_(r\d+)_/;
const LEGACY_CODE_PREFIX = /^R\d+\s*/;

export function normalizeStrategyName(strategyId: string, name: string): string {
  const match = AI_ROTATION_ID.exec(strategyId);
  if (!match) return name;
  const code = match[1].toUpperCase();
  const body = name.replace(LEGACY_CODE_PREFIX, "");
  return `${code} ${body}`.trim();
}
