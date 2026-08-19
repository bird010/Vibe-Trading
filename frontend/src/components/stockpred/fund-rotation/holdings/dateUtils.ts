export function dateTimestamp(value: string): number {
  const digits = String(value ?? "").replace(/\D/g, "").slice(0, 8);
  if (digits.length !== 8) return 0;
  const year = Number(digits.slice(0, 4));
  const month = Number(digits.slice(4, 6));
  const day = Number(digits.slice(6, 8));
  return Date.UTC(year, month - 1, day);
}

export function dateKeyAtTimestamp(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "";
  const date = new Date(value);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}`;
}
