import { useEffect, useState } from "react";
import { fetchByDate, fetchScanRuns, fetchToday, type ScanRun, type Signal, type TodayResponse } from "./api";

const fmtMoney = (n: number) => `$${n.toFixed(2)}`;
const fmtPct = (n: number) => `${(n * 100).toFixed(1)}%`;

function StatPill({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function SignalsTable({ signals }: { signals: Signal[] }) {
  if (signals.length === 0) {
    return <div className="empty">No qualifying signals on this date — bot waits, no forced fills.</div>;
  }
  return (
    <table className="signals">
      <thead>
        <tr>
          <th>#</th>
          <th>Ticker</th>
          <th className="num">Close</th>
          <th className="num">Target +30%</th>
          <th className="num">Stop −10%</th>
          <th className="num">Vol×</th>
          <th className="num">Base</th>
          <th className="num">Depth</th>
          <th className="num">Pullbacks</th>
          <th className="num">RS 60d</th>
        </tr>
      </thead>
      <tbody>
        {signals.map((s) => (
          <tr key={s.ticker}>
            <td className="rank">{s.rank}</td>
            <td className="ticker">{s.ticker}</td>
            <td className="num">{fmtMoney(s.close)}</td>
            <td className="num pos">{fmtMoney(s.target_price)}</td>
            <td className="num neg">{fmtMoney(s.stop_price)}</td>
            <td className="num strong">{s.volume_ratio.toFixed(2)}×</td>
            <td className="num">{s.base_weeks}w</td>
            <td className="num">{fmtPct(s.base_depth_pct)}</td>
            <td className="num">{s.pullback_count}</td>
            <td className="num">{s.rs_60d.toFixed(3)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ScanHistory({ runs, selectedDate, onSelect }: { runs: ScanRun[]; selectedDate: string | null; onSelect: (d: string) => void }) {
  return (
    <div className="history">
      <div className="history-title">Recent scans</div>
      <ul>
        {runs.map((r) => (
          <li
            key={r.scan_date}
            className={r.scan_date === selectedDate ? "active" : ""}
            onClick={() => onSelect(r.scan_date)}
          >
            <span className="date">{r.scan_date}</span>
            <span className="count">{r.signals_count} signals</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function App() {
  const [data, setData] = useState<TodayResponse | null>(null);
  const [runs, setRuns] = useState<ScanRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  useEffect(() => {
    let abort = false;
    setLoading(true);
    setError(null);
    const promise = selectedDate ? fetchByDate(selectedDate) : fetchToday();
    promise
      .then((d) => {
        if (abort) return;
        setData(d);
        setSelectedDate(d.scan_date);
      })
      .catch((e) => !abort && setError(String(e)))
      .finally(() => !abort && setLoading(false));
    return () => {
      abort = true;
    };
  }, [selectedDate]);

  useEffect(() => {
    fetchScanRuns().then(setRuns).catch(() => {});
  }, [data?.scan_date]);

  return (
    <div className="app">
      <header>
        <div>
          <h1>VCP Scanner</h1>
          <div className="subtitle">Volatility Contraction Pattern · Minervini SEPA · 10/30 R:R</div>
        </div>
        {data && (
          <div className="header-stats">
            <StatPill label="Scan date" value={data.scan_date} />
            <StatPill label="Universe" value={data.universe_size.toLocaleString()} />
            <StatPill label="Candidates" value={data.candidates_count} />
            <StatPill label="Signals" value={data.signals_count} />
            <StatPill label="SPY > 200d" value={data.spy_above_200 ? "yes" : "NO"} />
          </div>
        )}
      </header>

      <main>
        <ScanHistory runs={runs} selectedDate={selectedDate} onSelect={setSelectedDate} />

        <section className="signals-pane">
          {loading && <div className="loading">Loading…</div>}
          {error && <div className="error">⚠ {error}</div>}
          {!loading && !error && data && <SignalsTable signals={data.signals} />}
        </section>
      </main>

      <footer>
        Source: <a href="https://github.com/carrickcheah/trading-bot" target="_blank" rel="noreferrer">github.com/carrickcheah/trading-bot</a>
      </footer>
    </div>
  );
}
