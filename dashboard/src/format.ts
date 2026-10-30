// Small formatting helpers shared by the pages.

/** "45 s", "6 min 20 s", "1 h 5 min", "3 d 4 h". */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s} s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} min ${String(s % 60).padStart(2, "0")} s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

export function between(start: string, end: string | null, now: number = Date.now()): string {
  const finish = end ? Date.parse(end) : now;
  return duration((finish - Date.parse(start)) / 1000);
}

export function ago(time: string, now: number = Date.now()): string {
  return `${duration((now - Date.parse(time)) / 1000)} ago`;
}

/** "2026-10-30 02:11:28 UTC": incidents are compared across time zones, so always UTC. */
export function utc(time: string): string {
  return `${new Date(time).toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${Math.round(value * 100)}%`;
}
