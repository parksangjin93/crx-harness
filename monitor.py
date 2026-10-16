"""
테스트 모니터.

    python monitor.py

브라우저가 자동으로 열린다. 외부 CDN 을 안 쓴다.
QA PC 가 인터넷이 막혀 있어도 그대로 동작한다.

pytest 를 직접 실행하지 않는다. 이벤트 파일과 로그 파일을 읽기만 한다.
그래서 이 서버를 켜둔 채로 다른 터미널에서 pytest 를 돌리면 된다.
"""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from harness.events import read_events, tail_lines
from harness.store import Store

ROOT = Path(__file__).parent
ARTIFACTS = ROOT / "artifacts"

app = FastAPI(title="CRX Test Monitor")
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(ARTIFACTS / "runs.db")
    return _store


def _duts() -> list[dict]:
    """duts.yaml 정의와 artifacts 하위 폴더를 합쳐 DUT 목록을 만든다."""
    out, seen = [], set()
    cfg = ROOT / "config" / "duts.yaml"
    if cfg.exists():
        import yaml
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        for d in raw.get("duts", []):
            out.append({"name": d["name"], "product": d.get("product", "")})
            seen.add(d["name"])
    if ARTIFACTS.exists():
        for p in sorted(ARTIFACTS.iterdir()):
            if p.is_dir() and p.name not in seen:
                out.append({"name": p.name, "product": ""})
    return out


@app.get("/api/duts")
def api_duts():
    return JSONResponse({"duts": _duts()})


def _base_dir(dut: str) -> Path:
    """duts.yaml 의 log_dir 을 먼저 보고, 없으면 artifacts 밑."""
    cfg = ROOT / "config" / "duts.yaml"
    if cfg.exists():
        import yaml
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        for d in raw.get("duts", []):
            if d.get("name") == dut and d.get("log_dir"):
                return Path(d["log_dir"])
    return ARTIFACTS / dut


def _latest_file_mtime(p: Path) -> float:
    """
    폴더 자체의 수정시각이 아니라 안의 파일들로 판단한다. Windows 는
    폴더 안 파일 내용이 바뀌어도 폴더 자체의 수정시각은 안 바꾼다 -
    그래서 폴더 mtime 으로만 비교하면, 나중에 만들어졌지만 내용은 안
    바뀌는 빈 폴더가 실제로 계속 갱신 중인 폴더보다 "최신"으로 잘못
    뽑힌다 (2026-10 daemon 모드 겹쳐 실행했을 때 실제로 겪음).
    """
    times = [p.stat().st_mtime]
    ev = p / "events.jsonl"
    if ev.exists():
        times.append(ev.stat().st_mtime)
    for log in p.glob("*.log"):
        times.append(log.stat().st_mtime)
    return max(times)


def _run_dir(dut: str) -> Path:
    """
    가장 최근 실행 폴더. 산출물은 실행마다 시각 이름의 폴더로 들어간다.
    폴더가 없으면 base 를 그대로 돌려준다 (예전 구조 호환).
    """
    base = _base_dir(dut)
    if not base.is_dir():
        return base
    subs = [p for p in base.iterdir()
            if p.is_dir() and ((p / "events.jsonl").exists() or any(p.glob("*.log")))]
    if not subs:
        return base
    return max(subs, key=_latest_file_mtime)


@app.get("/api/events")
def api_events(dut: str = "", offset: int = 0):
    d = _run_dir(dut) if dut else ARTIFACTS
    events, new_offset = read_events(d / "events.jsonl", offset)
    # 실행 폴더가 바뀌면 클라이언트가 offset 을 되돌려야 한다
    return JSONResponse({"events": events, "offset": new_offset, "run": d.name})


def _log_path(dut: str) -> Path:
    """최근 실행 폴더의 최신 UART 로그."""
    for base in (_run_dir(dut), _base_dir(dut), ARTIFACTS / dut):
        if not base.is_dir():
            continue
        marker = base / "latest.txt"
        if marker.exists():
            p = base / marker.read_text(encoding="utf-8").strip()
            if p.exists():
                return p
        logs = sorted(base.glob("*.log"), key=lambda x: x.stat().st_mtime)
        if logs:
            return logs[-1]
    return ARTIFACTS / dut / "uart.log"


@app.get("/api/log")
def api_log(dut: str = "", n: int = 300):
    return JSONResponse({"lines": tail_lines(_log_path(dut), n)})


@app.get("/api/trend")
def api_trend():
    st = store()
    runs = st.runs(50)
    latest = runs[-1]["id"] if runs else None
    return JSONResponse({
        "runs": runs,
        "boot_time": st.capture_series("boot_time_s"),
        "flaky": st.flaky(),
        "failures": st.last_failures(latest) if latest else [],
    })


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


PAGE = r"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>CRX Test Monitor</title>
<style>
:root{--bg:#14151a;--fg:#e6e6e6;--dim:#8b8f98;--line:#2a2c35;--card:#1b1d24;
      --ok:#4ea672;--ng:#d05a52;--warn:#c8912f;--acc:#5b8dd6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:14px/1.6 "Malgun Gothic",system-ui,sans-serif}
header{display:flex;gap:4px;align-items:center;padding:10px 16px;
       border-bottom:1px solid var(--line)}
h1{font-size:15px;font-weight:500;margin:0 20px 0 0}
button.tab{background:none;border:none;color:var(--dim);padding:7px 14px;
     cursor:pointer;font-size:14px;border-radius:6px;font-family:inherit}
button.tab.on{background:var(--card);color:var(--fg)}
#dot{margin-left:auto;font-size:12px;color:var(--dim)}
main{padding:16px}
.grid{display:grid;grid-template-columns:340px 1fr;gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card h2{font-size:13px;font-weight:500;color:var(--dim);margin:0 0 10px}
#now{font-size:19px;font-weight:500}
#waitline{color:var(--warn);font-variant-numeric:tabular-nums;margin-top:6px}
#waitline b{font-size:22px}
.meta{color:var(--dim);font-size:12px;margin-top:4px;word-break:break-all}
#steps{list-style:none;padding:0;margin:10px 0 0;font-size:13px}
#steps li{padding:3px 0;border-bottom:1px solid var(--line);display:flex;gap:8px}
#steps li:last-child{border:none}
.badge{width:44px;flex:none;font-size:11px;text-align:center;border-radius:4px;
       padding:1px 0;align-self:center}
.b-ok{background:#1e3a2b;color:var(--ok)}.b-ng{background:#3a2020;color:var(--ng)}
.b-wait{background:#3a3018;color:var(--warn)}
#log{height:340px;overflow:auto;background:#0e0f13;
     border:1px solid var(--line);border-radius:8px;padding:10px;
     font:12px/1.5 Consolas,monospace;white-space:pre-wrap;word-break:break-all}
.l-err{color:var(--ng)}.l-hit{color:var(--ok)}.l-krn{color:var(--dim)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--dim);font-weight:500;padding:5px 8px;
   border-bottom:1px solid var(--line)}
td{padding:5px 8px;border-bottom:1px solid var(--line)}
tr:last-child td{border:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{padding:1px 7px;border-radius:9px;font-size:11px}
.hide{display:none}
#grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.dut{background:var(--card);border:1px solid var(--line);border-radius:10px;
     padding:12px;cursor:pointer}
.dut.sel{border-color:var(--acc)}
.dut.run{border-left:3px solid var(--ok)}
.dut.bad{border-left:3px solid var(--ng)}
.dut.idle{opacity:.62}
.dut h3{margin:0;font-size:14px;font-weight:500;display:flex;gap:8px;align-items:center}
.dut .pr{font-size:11px;color:var(--dim);border:1px solid var(--line);
         border-radius:4px;padding:0 5px}
.dut .st{margin-left:auto;font-size:11px;color:var(--dim)}
.tc{font-size:13px;margin-top:8px;min-height:20px}
.wt{color:var(--warn);font-size:12px;font-variant-numeric:tabular-nums;min-height:18px}
.vv{color:var(--dim);font-size:11px;word-break:break-all;min-height:16px}
.tl{margin-top:8px;font-size:11px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
.empty{color:var(--dim);padding:14px 0}
</style>
<header>
  <h1>CRX Test Monitor</h1>
  <button class="tab on" id="t1" onclick="tab(1)">실시간</button>
  <button class="tab" id="t2" onclick="tab(2)">추이</button>
  <span id="dot">대기 중</span>
</header>
<main>
  <div id="p1">
    <div id="grid"></div>
    <div class="card" style="margin-top:14px">
      <h2>UART 로그 &nbsp;<span id="logdut" style="color:var(--acc)"></span></h2>
      <div id="log"></div>
    </div>
  </div>

  <div id="p2" class="hide">
    <div class="card">
      <h2>FW 버전별 부팅 시간 (초)</h2>
      <div id="chart"></div>
    </div>
    <div class="row2">
      <div class="card">
        <h2>실행 이력</h2>
        <div id="runs"></div>
      </div>
      <div class="card">
        <h2>자꾸 깨지는 TC</h2>
        <div id="flaky"></div>
      </div>
    </div>
    <div class="card" style="margin-top:14px">
      <h2>최근 실행 실패 상세</h2>
      <div id="fails"></div>
    </div>
  </div>
</main>
<script>
let duts=[], st={}, sel=null, tick=0;
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function tab(n){
  t1.classList.toggle('on',n===1); t2.classList.toggle('on',n===2);
  p1.classList.toggle('hide',n!==1); p2.classList.toggle('hide',n!==2);
  if(n===2) trend();
}

async function boot(){
  const d=await (await fetch('/api/duts')).json();
  duts=d.duts;
  duts.forEach(x=>{ if(!st[x.name]) st[x.name]=
    {off:0,cur:null,steps:[],wait:null,seen:0,
     tally:{passed:0,failed:0,skipped:0,infra:0}}; });
  if(!sel && duts.length) sel=duts[0].name;
  drawGrid();
}

async function poll(){
  let live=0;
  for(const d of duts){
    const s=st[d.name];
    try{
      const r=await (await fetch('/api/events?dut='+encodeURIComponent(d.name)+'&offset='+s.off)).json();
      if(r.run && s.run && r.run!==s.run){
        // 새 실행이 시작됐다. 이전 실행의 누계를 이어붙이면 안 된다.
        s.off=0; s.cur=null; s.steps=[]; s.wait=null;
        s.tally={passed:0,failed:0,skipped:0,infra:0};
        const rr=await (await fetch('/api/events?dut='+encodeURIComponent(d.name)+'&offset=0')).json();
        s.run=rr.run; s.off=rr.offset;
        if(rr.events.length){s.seen=Date.now(); rr.events.forEach(e=>apply(s,e));}
        continue;
      }
      s.run=r.run; s.off=r.offset;
      if(r.events.length){s.seen=Date.now(); r.events.forEach(e=>apply(s,e));}
    }catch(e){}
    if(s.seen && (Date.now()-s.seen)/1000 < 8) live++;
  }
  dot.textContent = live? '● '+live+'대 실행 중' : '대기 중';
  dot.style.color = live? 'var(--ok)' : 'var(--dim)';
  drawGrid();
}

function apply(s,e){
  if(e.kind==='run_start'){s.cur=null;s.steps=[];s.tally={passed:0,failed:0,skipped:0,infra:0};}
  else if(e.kind==='tc_start'){s.cur=e;s.steps=[];s.wait=null;}
  else if(e.kind==='action'){
    s.wait = e.phase==='start' ? {value:'자극 '+e.action,elapsed:0,timeout:null} : null;}
  else if(e.kind==='step_wait'){s.wait={value:e.value,elapsed:0,timeout:e.timeout};
    s.steps.push({no:e.step,method:e.method,state:'wait'});}
  else if(e.kind==='waiting'){if(s.wait)Object.assign(s.wait,{elapsed:e.elapsed,timeout:e.timeout});}
  else if(e.kind==='step_done'){
    const x=s.steps.find(y=>y.no===e.step); if(x)x.state=e.passed?'ok':'ng';
    s.wait=null;}
  else if(e.kind==='tc_done'){s.tally[e.outcome]=(s.tally[e.outcome]||0)+1;s.wait=null;}
}

function drawGrid(){
  grid.innerHTML = duts.map(d=>{
    const s=st[d.name], idle=!s.seen||(Date.now()-s.seen)/1000>8;
    const cls='dut '+(d.name===sel?'sel ':'')+(idle?'idle':(s.tally.failed?'bad':'run'));
    const bars=s.steps.map(x=>'<span style="display:inline-block;width:14px;height:5px;'
      +'margin-right:2px;border-radius:2px;background:'
      +(x.state==='ok'?'#4ea672':x.state==='ng'?'#d05a52':'#c8912f')+'"></span>').join('');
    const t=s.tally;
    return '<div class="'+cls+'" onclick="pick(\''+d.name+'\')">'
      +'<h3>'+esc(d.name)+'<span class="pr">'+esc(d.product||'?')+'</span>'
      +'<span class="st">'+(idle?'대기':'실행 중')+'</span></h3>'
      +'<div class="tc">'+(s.cur?esc(s.cur.tc_id)+' '+esc(s.cur.name).slice(0,30):'-')+'</div>'
      +'<div class="wt">'+(s.wait?'기다리는 중 '+s.wait.elapsed.toFixed(0)+'초'
        +(s.wait.timeout?' / '+s.wait.timeout+'초':''):'')+'</div>'
      +'<div class="vv">'+(s.wait?esc(s.wait.value):'')+'</div>'
      +'<div class="tl">'+bars+'</div>'
      +'<div class="tl"><span style="color:#4ea672">통과 '+t.passed+'</span> &nbsp;'
      +'<span style="color:'+(t.failed?'#d05a52':'#8b8f98')+'">실패 '+t.failed+'</span> &nbsp;'
      +'<span style="color:#8b8f98">스킵 '+t.skipped+'</span></div></div>';
  }).join('') || '<div class="empty">config/duts.yaml 에 DUT 를 정의하세요</div>';
}

function pick(n){sel=n;drawGrid();pollLog();}

async function pollLog(){
  if(!sel){log.textContent='';return;}
  logdut.textContent=sel;
  try{
    const d=await (await fetch('/api/log?dut='+encodeURIComponent(sel)+'&n=250')).json();
    if(!d.lines.length){log.innerHTML='<span class="l-krn">artifacts/'+esc(sel)
      +'/uart.log 없음 (live 모드에서 생성됩니다)</span>';return;}
    const stick = log.scrollTop+log.clientHeight >= log.scrollHeight-40;
    log.innerHTML=d.lines.map(l=>{
      let c='';
      if(/Kernel panic|Call trace|Err |FAIL|error/i.test(l))c='l-err';
      else if(/LogWriter\)|U-Boot|Starting kernel/.test(l))c='l-hit';
      else if(/^\s*\d+\.\d+\s+\[/.test(l))c='l-krn';
      return '<div class="'+c+'">'+esc(l)+'</div>';}).join('');
    if(stick) log.scrollTop=log.scrollHeight;
  }catch(e){}
}

async function trend(){
  const d=await (await fetch('/api/trend')).json();
  chart.innerHTML = svgChart(d.boot_time);
  runs.innerHTML = d.runs.length? '<table><tr><th>#</th><th>DUT</th><th>FW</th>'
    +'<th class="num">통과</th><th class="num">실패</th><th class="num">스킵</th></tr>'
    + d.runs.slice().reverse().slice(0,12).map(r=>'<tr><td>'+r.id+'</td><td>'
    + esc(r.dut||'-')+'</td><td>'
    + esc(r.fw_version||'-')+'</td><td class="num" style="color:var(--ok)">'+r.passed
    +'</td><td class="num" style="color:'+(r.failed?'var(--ng)':'var(--dim)')+'">'+r.failed
    +'</td><td class="num" style="color:var(--dim)">'+r.skipped+'</td></tr>').join('')
    +'</table>' : '<div class="empty">실행 기록이 없습니다</div>';
  flaky.innerHTML = d.flaky.length? '<table><tr><th>TC</th><th class="num">실패/실행</th>'
    +'<th class="num">비율</th></tr>'+d.flaky.map(f=>'<tr><td>'+esc(f.tc_id)+'</td>'
    +'<td class="num">'+f.fails+' / '+f.runs+'</td><td class="num" style="color:'
    +(f.rate>=100?'var(--ng)':'var(--warn)')+'">'+f.rate+'%</td></tr>').join('')+'</table>'
    : '<div class="empty">2회 이상 실행돼야 집계됩니다</div>';
  fails.innerHTML = d.failures.length? '<table><tr><th>TC</th><th>사유</th></tr>'
    + d.failures.map(f=>'<tr><td style="white-space:nowrap">'+esc(f.tc_id)+'</td><td>'
    + esc((f.reason||'').split('\\n').filter(x=>/사유|기대|FAIL/.test(x)).slice(0,2).join(' / ')
      || (f.reason||'').slice(0,160))+'</td></tr>').join('')+'</table>'
    : '<div class="empty">실패 없음</div>';
}

function svgChart(pts){
  if(!pts||pts.length<1) return '<div class="empty">boot_time_s 캡처 데이터가 없습니다. '
    +'시트 CAPTURE 컬럼을 확인하세요</div>';
  const W=880,H=240,P=44, xs=pts.length>1?(W-P*2)/(pts.length-1):0;
  const vs=pts.map(p=>p.value), lo=Math.min(...vs)*0.97, hi=Math.max(...vs)*1.03||1;
  const y=v=>H-P-(v-lo)/((hi-lo)||1)*(H-P*2);
  const x=i=>P+i*xs;
  let g='';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4, yy=y(v);
    g+='<line x1="'+P+'" y1="'+yy+'" x2="'+(W-P)+'" y2="'+yy+'" stroke="#2a2c35"/>'
      +'<text x="'+(P-8)+'" y="'+(yy+4)+'" fill="#8b8f98" font-size="11" text-anchor="end">'
      +v.toFixed(1)+'</text>';}
  const d=pts.map((p,i)=>(i?'L':'M')+x(i)+' '+y(p.value)).join(' ');
  const dots=pts.map((p,i)=>'<circle cx="'+x(i)+'" cy="'+y(p.value)+'" r="3.5" fill="#5b8dd6">'
    +'<title>'+esc(p.fw)+' : '+p.value+'s</title></circle>').join('');
  const lbl=pts.map((p,i)=> (pts.length<=8||i%Math.ceil(pts.length/8)===0)
    ? '<text x="'+x(i)+'" y="'+(H-14)+'" fill="#8b8f98" font-size="10" text-anchor="middle">'
      +esc(String(p.fw).slice(0,12))+'</text>' : '').join('');
  return '<svg viewBox="0 0 '+W+' '+H+'" style="width:100%">'+g
    +'<path d="'+d+'" fill="none" stroke="#5b8dd6" stroke-width="2"/>'+dots+lbl+'</svg>';
}

boot(); setInterval(poll,500); setInterval(pollLog,400);
setInterval(boot,10000);
</script></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"모니터 실행 중 -> {url}   (Ctrl+C 로 종료)")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
