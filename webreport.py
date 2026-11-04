"""Investment report as a self-contained HTML page (inline SVG charts, no external assets), from SQLite only."""
from __future__ import annotations

import html
import json
import os
import sqlite3
from datetime import datetime, timezone

import report
from markets import MARKETS
from report import name

# Categorical slots (reference palette, light / dark). A ticker keeps its slot via its universe position,
# so colors follow the entity, never its rank.
SERIES = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
          ("#e87ba4", "#d55181"), ("#008300", "#008300"), ("#6250d6", "#9085e9"), ("#e34948", "#e66767")]
STATUS_CLASS = {"OK": "good", "SKIPPED": "warning", "PARTIAL": "serious", "FAILED": "critical", "RUNNING": "warning",
                "FILLED": "good", "CANCELLED": "warning", "PLANNED": "muted"}

CSS = """
:root{color-scheme:light;--bg:#f6f6f4;--surface-1:#fcfcfb;--border:#e4e3df;--text-primary:#0b0b0b;
--text-secondary:#52514e;--muted:#7a7975;--grid:#ecebe7;--good:#0ca30c;--warning:#b77d00;--serious:#c4562b;
--critical:#d03b3b;%(light)s}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--bg:#121211;
--surface-1:#1a1a19;--border:#2e2e2b;--text-primary:#f0efec;--text-secondary:#c3c2b7;--muted:#8f8e88;
--grid:#2a2a27;--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#e66767;%(dark)s}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#121211;--surface-1:#1a1a19;--border:#2e2e2b;
--text-primary:#f0efec;--text-secondary:#c3c2b7;--muted:#8f8e88;--grid:#2a2a27;--good:#0ca30c;
--warning:#fab219;--serious:#ec835a;--critical:#e66767;%(dark)s}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text-primary);
font:14px/1.5 system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0}h2{font-size:16px;margin:0 0 12px}h3{font-size:14px;margin:16px 0 8px;color:var(--text-secondary)}
.sub{color:var(--text-secondary);margin:4px 0 20px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:16px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:14px}
.tile .k{color:var(--text-secondary);font-size:12px}.tile .v{font-size:22px;font-weight:600;margin-top:2px;
font-variant-numeric:tabular-nums}.tile .d{font-size:12px;color:var(--text-secondary)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%%,460px),1fr));gap:16px}
.grid2>.card{margin-bottom:0}
table{width:100%%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);white-space:nowrap}
th{color:var(--text-secondary);font-weight:500;font-size:12px}td.n,th.n{text-align:right}
.scroll{overflow-x:auto}
.pill{display:inline-block;padding:0 8px;border-radius:999px;font-size:12px;border:1px solid currentColor}
.good{color:var(--good)}.warning{color:var(--warning)}.serious{color:var(--serious)}.critical{color:var(--critical)}
.muted{color:var(--muted)}
.wbar{position:relative;height:8px;background:var(--grid);border-radius:4px;min-width:90px}
.wbar span{position:absolute;left:0;top:0;bottom:0;background:var(--series-1);border-radius:4px}
.wbar i{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--text-primary)}
.chart{position:relative}.chart svg{display:block;width:100%%;height:auto}
.chart text{fill:var(--text-secondary);font-size:11px}
.tip{position:absolute;pointer-events:none;background:var(--surface-1);border:1px solid var(--border);
border-radius:8px;padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.12);display:none;white-space:nowrap}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary);margin-bottom:6px}
.legend b{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
details{margin-top:8px}summary{cursor:pointer;color:var(--text-secondary);font-size:12px}
.empty{color:var(--muted);padding:24px 0;text-align:center}
#live{margin-left:6px}
@media (max-width:560px){.barcol{display:none}th,td{padding:6px 5px}}
"""

JS = """
function initCharts(){
document.querySelectorAll('.chart[data-series]').forEach(function(el){
  var d=JSON.parse(el.dataset.series), svg=el.querySelector('svg'), tip=el.querySelector('.tip'),
      cross=svg.querySelector('.cross'), W=+svg.getAttribute('viewBox').split(' ')[2];
  svg.addEventListener('mousemove',function(e){
    var r=svg.getBoundingClientRect(), x=(e.clientX-r.left)*W/r.width, best=0, bd=1e9;
    d.x.forEach(function(px,i){var dd=Math.abs(px-x); if(dd<bd){bd=dd;best=i}});
    cross.setAttribute('x1',d.x[best]); cross.setAttribute('x2',d.x[best]); cross.style.display='';
    var h='<b>'+d.dates[best]+'</b>';
    d.series.forEach(function(s){ if(s.v[best]!==null) h+='<br><b style="color:'+s.c+'">\\u25A0</b> '+s.n+' '+s.f[best]; });
    tip.innerHTML=h; tip.style.display='block';
    var left=(e.clientX-r.left)+12; if(left+tip.offsetWidth>r.width) left-= tip.offsetWidth+24;
    tip.style.left=left+'px'; tip.style.top='8px';
  });
  svg.addEventListener('mouseleave',function(){tip.style.display='none'; cross.style.display='none'});
});
}
initCharts();
// Served page: poll /version and swap in the new page when SQLite changes (scroll and open tables kept).
(function(){
  var ver=document.body.dataset.version;
  if(!ver||location.protocol.indexOf('http')!==0) return;
  function stamp(ok){
    var el=document.getElementById('live'); if(!el) return;
    el.className='pill '+(ok?'good':'critical');
    var t=new Date(), p=function(n){return (n<10?'0':'')+n};
    el.textContent=ok?'실시간 · '+p(t.getHours())+':'+p(t.getMinutes())+':'+p(t.getSeconds())+' 확인'
                     :'서버 연결 끊김 · 재시도 중';
  }
  function swap(){
    return fetch('/',{cache:'no-store'}).then(function(r){return r.text()}).then(function(h){
      var doc=new DOMParser().parseFromString(h,'text/html'), main=doc.querySelector('main');
      if(!main) return;
      var open=[].map.call(document.querySelectorAll('main details'),function(d){return d.open});
      document.querySelector('main').replaceWith(main);
      [].forEach.call(document.querySelectorAll('main details'),function(d,i){if(open[i]) d.open=true});
      ver=doc.body.dataset.version; document.body.dataset.version=ver; initCharts();
    });
  }
  function poll(){
    fetch('/version',{cache:'no-store'}).then(function(r){if(!r.ok) throw r; return r.text()})
      .then(function(v){return v.trim()!==ver?swap():null}).then(function(){stamp(true)},function(){stamp(false)})
      .then(function(){setTimeout(poll,%(poll_ms)d)});
  }
  stamp(true); setTimeout(poll,%(poll_ms)d);
})();
"""
POLL_MS = 5000


def esc(v) -> str:
    return html.escape(str(v))


def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi == lo:
        return [lo]
    step = (hi - lo) / n
    return [lo + i * step for i in range(n + 1)]


def line_chart(dates: list[str], series: list[dict], fmt, height: int = 220) -> str:
    """series: [{name, slot, values}] (values aligned to dates, None allowed). One y-axis, 2px lines,
    end markers, crosshair tooltip via JS; legend when >= 2 series; table view in <details>."""
    if not dates or not any(v is not None for s in series for v in s["values"]):
        return '<div class="empty">데이터가 쌓이면 표시됩니다</div>'
    W, H, L, R, T, B = 720, height, 64, 16, 12, 26
    vals = [v for s in series for v in s["values"] if v is not None]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.02 or 1
    lo, hi = (max(lo - pad, 0) if lo >= 0 else lo - pad), hi + pad  # no negative axis for positive data
    xs = [L + (W - L - R) * (i / max(len(dates) - 1, 1)) for i in range(len(dates))]
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img">']
    for t in _ticks(lo, hi):
        parts.append(f'<line x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="var(--grid)"/>'
                     f'<text x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{esc(fmt(t))}</text>')
    for i in sorted({0, len(dates) // 2, len(dates) - 1}):
        anchor = "start" if i == 0 else "end" if i == len(dates) - 1 else "middle"
        parts.append(f'<text x="{xs[i]:.1f}" y="{H - 6}" text-anchor="{anchor}">{esc(dates[i][5:])}</text>')
    for s in series:
        pts = [(xs[i], y(v)) for i, v in enumerate(s["values"]) if v is not None]
        color = f"var(--series-{s['slot'] + 1})"
        if len(pts) > 1:
            path = " ".join(f"{'M' if k == 0 else 'L'}{px:.1f},{py:.1f}" for k, (px, py) in enumerate(pts))
            parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
                         f'stroke-linejoin="round" stroke-linecap="round"/>')
        px, py = pts[-1]
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{color}" stroke="var(--surface-1)" '
                     f'stroke-width="2"/>')
    parts.append(f'<line class="cross" y1="{T}" y2="{H - B}" stroke="var(--muted)" stroke-dasharray="3 3" '
                 f'style="display:none"/></svg>')
    data = {"x": [round(v, 1) for v in xs], "dates": dates,
            "series": [{"n": name(s["name"]), "c": f"var(--series-{s['slot'] + 1})",
                        "v": s["values"], "f": [fmt(v) if v is not None else "" for v in s["values"]]}
                       for s in series]}
    legend = ""
    if len(series) >= 2:
        legend = '<div class="legend">' + "".join(
            f'<span><b style="background:var(--series-{s["slot"] + 1})"></b>{esc(name(s["name"]))}</span>'
            for s in series) + "</div>"
    table = ('<details><summary>표로 보기</summary><div class="scroll"><table><tr><th>날짜</th>'
             + "".join(f'<th class="n">{esc(name(s["name"]))}</th>' for s in series) + "</tr>"
             + "".join(f"<tr><td>{esc(d)}</td>" + "".join(
                 f'<td class="n">{esc(fmt(s["values"][i])) if s["values"][i] is not None else ""}</td>'
                 for s in series) + "</tr>" for i, d in enumerate(dates))
             + "</table></div></details>")
    return (f"{legend}<div class=\"chart\" data-series='{esc(json.dumps(data, ensure_ascii=False))}'>"
            f"{''.join(parts)}<div class=\"tip\"></div></div>{table}")


def _won_axis(v: float) -> str:
    """One unit per axis: 만원 (₩9,689만) reads at a glance and never mixes with 억/원."""
    return f"₩{v / 1e4:,.0f}만"


def market_card(conn: sqlite3.Connection, market: str) -> str:
    universe = list(MARKETS[market].universe)
    slot = {t: i for i, t in enumerate(universe)}
    lines, value, warns = report.market_section(conn, market)
    head = esc(lines[0].split(" ", 1)[1])  # drop the flag emoji: Windows renders it as two letters
    if value is None:
        return f'<section class="card"><h2>{head}</h2><div class="empty">기록 없음</div></section>'

    pos_date = conn.execute("SELECT MAX(date) FROM positions WHERE market = ?", (market,)).fetchone()[0]
    pos = conn.execute("SELECT ticker, qty, price, value FROM positions WHERE market = ? AND date = ? "
                       "ORDER BY value DESC", (market, pos_date)).fetchall()
    tgt_session = conn.execute("SELECT MAX(session_date) FROM targets WHERE market = ?", (market,)).fetchone()[0]
    targets = {r["ticker"]: r["weight"] for r in conn.execute(
        "SELECT ticker, weight FROM targets WHERE market = ? AND session_date = ?", (market, tgt_session))}
    rows = []
    for r in pos:
        w = r["value"] / value if value else 0
        tw = targets.get(r["ticker"])
        bar = (f'<div class="wbar"><span style="width:{w * 100:.1f}%"></span>'
               + (f'<i style="left:{tw * 100:.1f}%"></i>' if tw is not None else "") + "</div>")
        qty = "" if r["ticker"] == "CASH" else f"{r['qty']:,.0f}"
        rows.append(f"<tr><td>{esc('현금' if r['ticker'] == 'CASH' else name(r['ticker']))}</td>"
                    f'<td class="n">{qty}</td><td class="n">{esc(report.money(r["value"], market))}</td>'
                    f'<td class="n">{w:.1%}</td><td class="n">{"" if tw is None else f"{tw:.0%}"}</td>'
                    f'<td class="barcol">{bar}</td></tr>')
    holdings = ('<div class="scroll"><table><tr><th>종목</th><th class="n">수량</th><th class="n">평가액</th>'
                '<th class="n">비중</th><th class="n">목표</th><th class="barcol">비중 · 목표(선)</th></tr>'
                + "".join(rows) + "</table></div>")

    # market-cap history of today's top 3 (<= 3 series keeps the palette inside its all-pairs-safe range)
    latest = conn.execute("SELECT MAX(session_date) FROM rankings WHERE market = ?", (market,)).fetchone()[0]
    top = [r[0] for r in conn.execute("SELECT ticker FROM rankings WHERE market = ? AND session_date = ? "
                                      "ORDER BY rank LIMIT 3", (market, latest))]
    dates = [r[0] for r in conn.execute("SELECT DISTINCT session_date FROM rankings WHERE market = ? "
                                        "ORDER BY session_date DESC LIMIT 120", (market,))][::-1]
    caps = {(r["session_date"], r["ticker"]): r["mcap"] for r in conn.execute(
        "SELECT session_date, ticker, mcap FROM rankings WHERE market = ? AND session_date >= ?",
        (market, dates[0] if dates else ""))}
    series = [{"name": t, "slot": slot.get(t, 0) % len(SERIES),
               "values": [caps.get((d, t)) for d in dates]} for t in top]
    chart = line_chart(dates, series, lambda v: report.mcap(v, market))

    trade = next((l.strip() for l in lines if l.strip().startswith("체결")), "")
    rank = next((l.strip() for l in lines if l.strip().startswith("순위")), "")
    warn = "".join(f'<div class="serious">⚠ {esc(w)}</div>' for w in warns)
    return (f'<section class="card"><h2>{head}</h2>{warn}<h3>보유</h3>{holdings}'
            f'<h3>최근 체결</h3><div>{esc(trade)}</div><h3>시총 상위 3 추이</h3>'
            f'<div class="sub" style="margin:0 0 6px">{esc(rank)}</div>{chart}</section>')


def build_html(conn: sqlite3.Connection, usdkrw: float | None = None, version: str | None = None) -> str:
    """version: the served page's SQLite version (see db_version); the page polls /version and refreshes
    itself when it changes. None (a written file) = static page."""
    eq = conn.execute("SELECT * FROM equity ORDER BY date").fetchall()
    kr_val, us_val = report.pocket_values(conn)
    rate = usdkrw or (eq[-1]["usdkrw"] if eq else None)
    total = kr_val + us_val * rate if rate and kr_val is not None and us_val is not None else None

    tiles = []
    if total is not None:
        tiles.append(("총자산", f"₩{total:,.0f}", f"원화 {report.money(kr_val, 'KR')} · 달러 {report.money(us_val, 'US')}"))
    if len(eq) >= 2:
        tiles.append(("직전 대비", report.pct(eq[-1]["total"] / eq[-2]["total"] - 1), f"{eq[-2]['date']} → {eq[-1]['date']}"))
    if eq:
        tiles.append(("누적 (첫 보고 대비)", report.pct(eq[-1]["total"] / eq[0]["total"] - 1), f"{eq[0]['date']} 부터"))
    if rate:
        tiles.append(("USD/KRW", f"{rate:,.1f}", "보고 시점 환율" if not usdkrw else "현재"))
    tile_html = "".join(f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div>'
                        f'<div class="d">{esc(d)}</div></div>' for k, v, d in tiles)

    equity_chart = line_chart([r["date"] for r in eq], [{"name": "총자산", "slot": 0,
                                                       "values": [r["total"] for r in eq]}], _won_axis)

    orders = conn.execute("SELECT * FROM orders ORDER BY session_date DESC, id DESC LIMIT 40").fetchall()
    order_rows = "".join(
        f"<tr><td>{esc(o['session_date'])}</td><td>{esc(o['market'])}</td><td>{esc(name(o['ticker']))}</td>"
        f"<td>{'매수' if o['side'] == 'BUY' else '매도'}</td><td class=\"n\">{o['step']}</td>"
        f"<td class=\"n\">{o['filled_qty']}/{o['qty']}</td>"
        f"<td class=\"n\">{esc(report.price(o['limit_price'], o['market']))}</td>"
        f"<td class=\"n\">{esc(report.price(o['fill_price'], o['market'])) if o['fill_price'] else ''}</td>"
        f"<td><span class=\"pill {STATUS_CLASS.get(o['status'], 'muted')}\">{esc(o['status'])}</span></td></tr>"
        for o in orders)
    orders_html = ('<div class="scroll"><table><tr><th>세션</th><th>시장</th><th>종목</th><th>구분</th>'
                   '<th class="n">단계</th><th class="n">체결/주문</th><th class="n">지정가</th><th class="n">체결가</th>'
                   '<th>상태</th></tr>' + order_rows + "</table></div>") if orders else '<div class="empty">주문 없음</div>'

    runs = conn.execute("SELECT * FROM runs ORDER BY session_date DESC, started_at DESC LIMIT 30").fetchall()
    run_rows = "".join(
        f"<tr><td>{esc(r['session_date'])}</td><td>{esc(r['market'])}</td><td>{esc(r['job'])}</td>"
        f"<td><span class=\"pill {STATUS_CLASS.get(r['status'], 'muted')}\">{esc(r['status'])}</span></td>"
        f"<td style=\"white-space:normal\">{esc((r['detail'] or '')[:160])}</td></tr>" for r in runs)
    runs_html = ('<div class="scroll"><table><tr><th>세션</th><th>시장</th><th>작업</th><th>상태</th><th>내용</th></tr>'
                 + run_rows + "</table></div>") if runs else '<div class="empty">실행 기록 없음</div>'

    light = "".join(f"--series-{i + 1}:{lt};" for i, (lt, _) in enumerate(SERIES))
    dark = "".join(f"--series-{i + 1}:{dk};" for i, (_, dk) in enumerate(SERIES))
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>투자 리포트</title>
<style>{CSS % {"light": light, "dark": dark}}</style></head>
<body{f' data-version="{esc(version)}"' if version else ""}><main>
<h1>투자 리포트</h1><p class="sub">시총 1위 + 채권 ETF 리밸런싱 · 생성 {esc(generated)} · 평가는 마지막 체결 시점 가격
{'<span id="live" class="pill good"></span>' if version else ""}</p>
<div class="tiles">{tile_html}</div>
<section class="card"><h2>총자산 추이 (원화 환산)</h2>{equity_chart}</section>
<div class="grid2">{market_card(conn, "KR")}{market_card(conn, "US")}</div>
<section class="card" style="margin-top:16px"><h2>주문 내역</h2>{orders_html}</section>
<section class="card"><h2>작업 기록</h2>{runs_html}</section>
</main><script>{JS % {"poll_ms": POLL_MS}}</script></body></html>"""


def write_site(conn: sqlite3.Connection, out_dir, usdkrw: float | None = None):
    """Writes out_dir/index.html; returns its path."""
    from pathlib import Path
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(build_html(conn, usdkrw), encoding="utf-8")
    return path


def db_version(db_path) -> str:
    """Changes whenever a job commits to the database (file size/mtime incl. WAL and rollback journal)."""
    from pathlib import Path
    parts = []
    for suffix in ("", "-wal", "-journal"):
        p = Path(f"{db_path}{suffix}")
        if p.exists():
            st = p.stat()
            parts.append(f"{st.st_mtime_ns:x}.{st.st_size:x}")
    return "-".join(parts) or "none"


def render(db_path) -> str:
    """The served page. Opened read-only, so the server never blocks a trading job's writes."""
    from pathlib import Path
    version = db_version(db_path)
    if not Path(db_path).exists():
        return _message_page(f"DB 없음: {db_path} — jobs.py init 으로 먼저 만드세요.", version)
    conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return build_html(conn, version=version)
    except sqlite3.Error as e:  # e.g. a DB from an older schema, or locked past the timeout
        return _message_page(f"DB 읽기 실패: {e}", version)
    finally:
        conn.close()


def _message_page(text: str, version: str) -> str:
    """Keeps polling, so the page recovers by itself once the DB is there."""
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>투자 리포트</title>'
            f'<style>{CSS % {"light": "", "dark": ""}}</style></head><body data-version="{esc(version)}"><main>'
            f'<h1>투자 리포트</h1><p class="sub"><span id="live" class="pill good"></span></p>'
            f'<div class="card empty">{esc(text)}</div></main><script>{JS % {"poll_ms": POLL_MS}}</script>'
            f'</body></html>')


def make_server(db_path, host: str = "127.0.0.1", port: int = 8765):
    """GET / = the page rebuilt from SQLite on every request, GET /version = db_version (the page polls it)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                body, ctype = render(db_path).encode("utf-8"), "text/html; charset=utf-8"
            elif path == "/version":
                body, ctype = db_version(db_path).encode(), "text/plain"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the console quiet
            pass

    class Server(ThreadingHTTPServer):
        allow_reuse_address = os.name != "nt"  # on Windows SO_REUSEADDR would let a second server share the port
        daemon_threads = True

    return Server((host, port), Handler)


def serve(db_path, host: str = "127.0.0.1", port: int = 8765) -> None:
    try:
        server = make_server(db_path, host, port)
    except OSError as e:  # most often: already running (scheduled task or another terminal)
        raise SystemExit(f"cannot listen on {host}:{port} ({e.strerror}); already running? open http://{host}:{port}/")
    print(f"serving http://{host}:{port}/  (Ctrl+C to stop; an open page refreshes itself when the DB changes)")
    server.serve_forever()
