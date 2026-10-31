export const PAGE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Edge Fleet</title>
<style>
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6a64;--line:#e3e1da;--ok:#1f8a5b;--bad:#c2412d;--warn:#b7791f;--accent:#3554d1;--base:#9a988f}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--card:#1c1c1a;--ink:#ecebe6;--mute:#9c9a92;--line:#2c2b28;--ok:#3fbf86;--bad:#e8664f;--warn:#e0a23d;--accent:#7d93ff;--base:#77756e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0}.sub{color:var(--mute);margin:4px 0 18px;max-width:900px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-bottom:16px}
.cmp{display:grid;grid-template-columns:1fr auto auto auto;gap:4px 14px;font-variant-numeric:tabular-nums;align-items:baseline}
.cmp .k{color:var(--mute);font-size:12px}.cmp b{font-size:20px}
.phase{margin:22px 0 10px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.phase h2{margin:0}.phase span{color:var(--mute);font-size:12px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:820px){.cols{grid-template-columns:1fr}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:8px}
.tile{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:8px;padding:8px 10px;min-height:110px}
.tile.summit{border-left-color:var(--ok)}.tile.fell{border-left-color:var(--bad)}.tile.stalled{border-left-color:var(--warn)}
.tile .h{display:flex;justify-content:space-between;font-weight:600;gap:6px}.tile .o{font-size:12px;font-weight:500}
.summit .o{color:var(--ok)}.fell .o{color:var(--bad)}.stalled .o{color:var(--warn)}
.bar{height:5px;background:var(--line);border-radius:3px;margin:6px 0}.bar i{display:block;height:100%;background:var(--accent);border-radius:3px}
.tile p{margin:2px 0;font-size:12px;color:var(--mute)}.tile p.c{color:var(--ink)}
.rules ol{margin:6px 0 0;padding-left:20px}.rules li{margin:3px 0}
.lat{display:flex;gap:8px;align-items:flex-end;height:110px;margin-top:8px}
.lat div{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;font-size:11px;color:var(--mute)}
.lat em{width:100%;border-radius:3px 3px 0 0;background:var(--accent);min-height:2px}
.lat em.bad{background:var(--bad)}
</style></head><body><main>
<h1>Edge Fleet</h1>
<p class="sub">Simulated Unitree G1 humanoids climb a 35&deg; fixed-line slope, each with two hidden faults and its own Claude agent. The walking policy runs on the robot (edge); agents read only telemetry and change one setting per round. The simulator is the judge: a summit is 1.5 m of ascent without falling.</p>
<div class="card" style="margin-bottom:16px"><h2>60-second demo</h2><video controls preload="metadata" playsinline style="width:100%;max-width:960px;display:block;border-radius:8px;background:#000" id="demo"></video></div>
<div class="row">
 <div class="card"><h2>Why reflexes stay on the edge</h2><div class="lat" id="lat"><div>latency sweep pending</div></div><p class="sub" style="margin:6px 0 0;font-size:12px">Healthy robots, same seeds. Bar = share of runs that summit at each action delay (control steps); number above = mean steps to summit.</p></div>
 <div class="card"><h2>Train: solo vs shared rulebook</h2><div class="cmp" id="cmp-train"></div></div>
 <div class="card"><h2>Held-out: new robots, new faults</h2><div class="cmp" id="cmp-holdout"></div></div>
</div>
<div id="phase-train"></div>
<div class="card rules" style="margin-top:14px"><h2>Fleet rulebook (coordinator agent)</h2><ol id="rules"><li>waiting for round 1</li></ol></div>
<div id="phase-holdout"></div>
</main><script>
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt=v=>v===null||v===undefined?"-":v;
function tile(r){const solved=r.solved_round;const cls=solved?"summit":r.outcome;
 const pct=Math.max(0,Math.min(100,(r.ascent_m/1.5)*100));
 return '<div class="tile '+cls+'"><div class="h"><span>G1 #'+r.robot+'</span><span class="o">'+(solved?"summit r"+solved:esc(r.outcome))+'</span></div>'+
 '<div class="bar"><i style="width:'+(solved?100:pct)+'%"></i></div>'+
 (solved?'<p>hidden faults: '+esc(r.fault)+'</p>':'')+
 (r.change?'<p class="c">set '+esc(r.change)+'</p>':'')+(r.diagnosis?'<p>'+esc(r.diagnosis)+'</p>':'')+'</div>';}
function merged(ph){const last=ph.rounds[ph.rounds.length-1];const base=last?last.robots:ph.reset.robots;const keep={};
 base.forEach(r=>{keep[r.robot]=r});ph.rounds.forEach(rd=>rd.robots.forEach(r=>{if(r.solved_round&&!keep[r.robot].solved_round)keep[r.robot]=r}));
 return Object.values(keep).sort((a,b)=>a.robot-b.robot);}
function renderPhase(id,ph,title){const el=document.getElementById("phase-"+id);const cmp=document.getElementById("cmp-"+id);
 if(!ph){el.innerHTML="";cmp.innerHTML='<span class="k">not started</span>';return;}
 const g=ph.reset.groups||["solo","shared"];const last=ph.rounds[ph.rounds.length-1];const robots=merged(ph);
 el.innerHTML='<div class="phase"><h2>'+title+'</h2><span>round '+(last?last.round:0)+' / '+ph.reset.rounds+'</span></div><div class="cols">'+
  g.map(name=>'<section><h2>'+esc(name)+'</h2><div class="grid">'+robots.filter(r=>r.group===name).map(tile).join("")+'</div></section>').join("")+'</div>';
 const st=last?last.stats:null;
 cmp.innerHTML='<span class="k"></span><span class="k">summited</span><span class="k">mean rounds</span><span class="k">calls / summit</span>'+
  g.map(name=>{const s=st?st[name]:null;return '<span>'+esc(name)+'</span><b>'+(s?s.solved+"/"+s.of:"0/12")+'</b><span>'+fmt(s&&s.mean_rounds)+'</span><span>'+fmt(s&&s.calls_per_summit)+'</span>';}).join("");
 if(id==="train"&&last)document.getElementById("rules").innerHTML=last.rulebook.length?last.rulebook.map(r=>"<li>"+esc(r)+"</li>").join(""):"<li>empty</li>";}
async function tick(){try{const s=await (await fetch("/state",{cache:"no-store"})).json();
 const vid=document.getElementById("demo");if(s.video&&vid.dataset.src!==s.video){vid.dataset.src=s.video;vid.src=s.video;}
 if(s.latency){const rows=s.latency.rows;document.getElementById("lat").innerHTML=rows.map(r=>'<div title="mean steps to summit: '+fmt(r.mean_steps_to_summit)+'"><span>'+r.summits+'/'+r.runs+'</span><span>'+(r.mean_steps_to_summit?r.mean_steps_to_summit+"st":"")+'</span><em class="'+(r.summits===0?"bad":"")+'" style="height:'+(r.summits/r.runs*70)+'%"></em><span>'+r.lag_steps+'</span></div>').join("");}
 renderPhase("train",s.train,"Phase 1 &middot; train");renderPhase("holdout",s.holdout,"Phase 2 &middot; held-out robots");
}catch(e){}}
tick();setInterval(tick,2000);
</script></body></html>`;
