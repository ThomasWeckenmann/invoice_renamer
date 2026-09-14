/** Small display-formatting helpers shared across batch-workspace components. */

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${exponent === 0 ? value : value.toFixed(1)} ${units[exponent]}`;
}

export function formatAmount(amount: string | null, currency: string | null): string {
  if (amount === null) return "—";
  return currency ? `${amount} ${currency}` : amount;
}
