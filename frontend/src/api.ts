export interface Signal {
  rank: number;
  ticker: string;
  close: number;
  target_price: number;
  stop_price: number;
  base_low: number;
  base_high: number;
  base_weeks: number;
  base_depth_pct: number;
  pullback_count: number;
  volume_ratio: number;
  rs_60d: number;
}

export interface TodayResponse {
  scan_date: string;
  spy_above_200: boolean;
  universe_size: number;
  candidates_count: number;
  signals_count: number;
  signals: Signal[];
}

export interface ScanRun {
  scan_date: string;
  started_at: string;
  finished_at: string | null;
  universe_size: number;
  candidates_count: number;
  signals_count: number;
  spy_above_200: boolean;
  duration_seconds: number | null;
  error: string | null;
}

export async function fetchToday(): Promise<TodayResponse> {
  const r = await fetch("/api/signals/today");
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function fetchByDate(scanDate: string): Promise<TodayResponse> {
  const r = await fetch(`/api/signals/${scanDate}`);
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function fetchScanRuns(limit = 30): Promise<ScanRun[]> {
  const r = await fetch(`/api/scan-runs?limit=${limit}`);
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`);
  return r.json();
}
