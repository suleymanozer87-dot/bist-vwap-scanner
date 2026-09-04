# -*- coding: utf-8 -*-
"""
chart_helpers.py — V5.0 custom Canvas chart engine.

Amaç:
- Plotly mobil gesture kısıtlarını tamamen kaldırmak.
- iPhone/Android'de iki parmak pinch zoom'u doğrudan JS touch eventleriyle yönetmek.
- Sağ fiyat ekseninden sürükleyerek TradingView benzeri dikey ölçekleme sağlamak.
- Ayrı hacim panelini tamamen kaldırmak.
- Normal fiyat mumlarının GÖVDE genişliğini hacme göre değiştirmek:
  yüksek hacim = daha şişkin mum, düşük hacim = daha ince mum.

Tarama/sinyal mantığı bu dosyada değildir; sadece sonuç grafikleri çizilir.
"""

from __future__ import annotations

import json
import math
import hashlib
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit.components.v1 as components

VWAP_COLORS = {1: "#18a864", 2: "#2979ff", 3: "#d99b16"}
CURRENCY_AXIS_LABELS = {"TRY": "TL", "USD": "$", "EUR": "€"}

UP_COLOR_LOW_VOL = "#7ad9a7"
UP_COLOR_HIGH_VOL = "#08783b"
DOWN_COLOR_LOW_VOL = "#f09a93"
DOWN_COLOR_HIGH_VOL = "#b3261e"
LAST_UP = "#16b86c"
LAST_DOWN = "#e34b43"


def _finite(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _blend_hex(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, float(t)))
    av = tuple(int(a[i:i+2], 16) for i in (1, 3, 5))
    bv = tuple(int(b[i:i+2], 16) for i in (1, 3, 5))
    mv = tuple(round(av[i] + (bv[i] - av[i]) * t) for i in range(3))
    return f"#{mv[0]:02x}{mv[1]:02x}{mv[2]:02x}"


def _volume_rank(volume: pd.Series) -> List[float]:
    """0..1 percentile rank. Hacim dağılımı çarpık olsa da genişlik farkı görünür."""
    v = pd.to_numeric(volume, errors="coerce").fillna(0.0)
    if len(v) <= 1:
        return [1.0] * len(v)
    r = v.rank(pct=True, method="average").clip(0.0, 1.0)
    return [float(x) for x in r]


def _date_label(ts: Any, intraday: bool) -> str:
    try:
        p = pd.Timestamp(ts)
        if intraday:
            return p.strftime("%d.%m %H:%M")
        return p.strftime("%d.%m.%Y")
    except Exception:
        return str(ts)


def _candles_from_df(df: pd.DataFrame, period: str = "") -> List[Dict[str, Any]]:
    intraday = str(period) in ("1h", "4h")
    ranks = _volume_rank(df.get("Volume", pd.Series([0] * len(df))))
    out: List[Dict[str, Any]] = []
    for i, row in df.reset_index(drop=True).iterrows():
        o = _finite(row.get("Open")); h = _finite(row.get("High")); l = _finite(row.get("Low")); c = _finite(row.get("Close"))
        if None in (o, h, l, c):
            continue
        vol = _finite(row.get("Volume"), 0.0) or 0.0
        rank = ranks[i] if i < len(ranks) else 0.5
        up = c >= o
        color = _blend_hex(UP_COLOR_LOW_VOL if up else DOWN_COLOR_LOW_VOL,
                           UP_COLOR_HIGH_VOL if up else DOWN_COLOR_HIGH_VOL,
                           rank)
        out.append({
            "i": int(i),
            "label": _date_label(row.get("Date", i), intraday),
            "o": o, "h": h, "l": l, "c": c, "v": vol,
            "vr": round(float(rank), 6), "color": color,
        })
    return out


def _series_points(series: Any) -> List[Dict[str, float]]:
    pts: List[Dict[str, float]] = []
    try:
        vals = series.values if hasattr(series, "values") else list(series)
        for i, val in enumerate(vals):
            y = _finite(val)
            if y is not None:
                pts.append({"i": int(i), "y": y})
    except Exception:
        pass
    return pts


def _base_spec(df: pd.DataFrame, period: str, focus_start: Optional[int] = None,
               focus_end: Optional[int] = None, currency: str = "TRY") -> Dict[str, Any]:
    candles = _candles_from_df(df, period)
    n = len(candles)
    if n == 0:
        return {"candles": [], "lines": [], "markers": [], "rects": [], "focus": [0, 1]}
    if focus_start is None:
        focus_start = max(0, n - 80)
    if focus_end is None:
        focus_end = n - 1
    focus_start = max(0, min(n - 1, int(focus_start)))
    focus_end = max(focus_start, min(n - 1, int(focus_end)))
    # En az 45, en fazla 105 mumluk başlangıç görünümü.
    if focus_end - focus_start + 1 < 45:
        focus_start = max(0, focus_end - 44)
    if focus_end - focus_start + 1 > 105:
        focus_start = max(0, focus_end - 104)
    return {
        "candles": candles,
        "lines": [],
        "markers": [],
        "rects": [],
        "focus": [focus_start, focus_end + 2],
        "currency": CURRENCY_AXIS_LABELS.get(currency, currency),
        "lastPrice": candles[-1]["c"],
        "lastUp": bool(candles[-1]["c"] >= candles[-1]["o"]),
    }


def _add_line(spec: Dict[str, Any], points: List[Dict[str, float]], color: str,
              width: float = 2.0, dashed: bool = False) -> None:
    if len(points) >= 2:
        spec["lines"].append({"points": points, "color": color, "width": width, "dashed": dashed})


def _add_marker(spec: Dict[str, Any], i: int, y: float, color: str, shape: str = "triangle") -> None:
    yy = _finite(y)
    if yy is not None:
        spec["markers"].append({"i": int(i), "y": yy, "color": color, "shape": shape})


def _render_canvas_chart(spec: Dict[str, Any], key: Optional[str] = None, height: int = 680) -> None:
    """Self-contained Canvas chart. No Plotly, no external JS dependency."""
    raw_key = str(key or "chart")
    uid = "bistcanvas_" + hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:12]
    payload = json.dumps(spec, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    html = f"""
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<div id="{uid}_wrap" class="bist-wrap">
  <div id="{uid}_chart" class="bist-chart" aria-label="Etkileşimli fiyat grafiği">
    <canvas id="{uid}_canvas"></canvas>
    <div id="{uid}_tip" class="bist-tip"></div>
    <button id="{uid}_reset" class="chart-btn reset-btn" type="button" aria-label="Grafiği sıfırla">↺</button>
    <button id="{uid}_fs" class="chart-btn fs-btn" type="button" aria-label="Tam ekran">⛶</button>
  </div>
</div>
<style>
  html,body{{margin:0;padding:0;background:#fff;overflow:hidden;-webkit-text-size-adjust:100%;}}
  .bist-wrap{{width:100%;height:{height}px;background:#fff;}}
  .bist-chart{{position:relative;width:100%;height:100%;background:#fff;overflow:hidden;touch-action:none;overscroll-behavior:contain;user-select:none;-webkit-user-select:none;}}
  .bist-chart canvas{{position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none;}}
  .chart-btn{{position:absolute;top:8px;z-index:5;width:36px;height:36px;border:1px solid #d6dde7;border-radius:9px;background:rgba(255,255,255,.92);color:#657285;font-size:21px;line-height:32px;padding:0;}}
  .reset-btn{{right:50px}} .fs-btn{{right:8px}}
  .bist-tip{{position:absolute;display:none;z-index:6;pointer-events:none;left:8px;top:8px;max-width:70%;background:rgba(255,255,255,.94);border:1px solid #d6dde7;border-radius:8px;padding:6px 8px;font:12px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1b2638;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
  @media(max-width:760px){{
    .bist-wrap{{height:{max(height, 720)}px}}
    .chart-btn{{width:42px;height:42px;font-size:23px}}
    .reset-btn{{right:58px}}
  }}
  :fullscreen .bist-chart, .bist-chart:fullscreen{{width:100vw!important;height:100vh!important;background:#fff}}
</style>
<script>
(() => {{
  const spec = {payload};
  const wrap = document.getElementById('{uid}_wrap');
  const host = document.getElementById('{uid}_chart');
  const canvas = document.getElementById('{uid}_canvas');
  const tip = document.getElementById('{uid}_tip');
  const resetBtn = document.getElementById('{uid}_reset');
  const fsBtn = document.getElementById('{uid}_fs');
  const ctx = canvas.getContext('2d');
  const C = spec.candles || [];
  if (!C.length) {{ ctx.font='14px sans-serif'; ctx.fillStyle='#6b7280'; ctx.fillText('Grafik verisi yok',20,30); return; }}

  let dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  let W=0,H=0, plot={{l:8,r:70,t:12,b:34,w:0,h:0}};
  let xMin = Number(spec.focus?.[0] ?? Math.max(0,C.length-80));
  let xMax = Number(spec.focus?.[1] ?? C.length+1);
  let yMin=0, yMax=1, manualY=false;
  let pan=null, axisDrag=null, pinch=null, mousePan=null, mouseAxis=null;
  let lastTapAxis=0;
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));

  function resize() {{
    const r=host.getBoundingClientRect(); W=Math.max(280,r.width); H=Math.max(360,r.height);
    dpr=Math.max(1,Math.min(3,window.devicePixelRatio||1));
    canvas.width=Math.round(W*dpr); canvas.height=Math.round(H*dpr);
    canvas.style.width=W+'px'; canvas.style.height=H+'px';
    ctx.setTransform(dpr,0,0,dpr,0,0);
    plot.r = W < 520 ? 62 : 72; plot.l = W < 520 ? 4 : 8; plot.t=8; plot.b=W<520?32:38;
    plot.w=Math.max(80,W-plot.l-plot.r); plot.h=Math.max(80,H-plot.t-plot.b);
    if(!manualY) autoY(); draw();
  }}

  function visibleIndices() {{
    const a=Math.max(0,Math.floor(xMin)-2), b=Math.min(C.length-1,Math.ceil(xMax)+2); return [a,b];
  }}
  function autoY() {{
    const [a,b]=visibleIndices(); let lo=Infinity,hi=-Infinity;
    for(let i=a;i<=b;i++){{ lo=Math.min(lo,C[i].l); hi=Math.max(hi,C[i].h); }}
    if(!Number.isFinite(lo)||!Number.isFinite(hi)||hi<=lo){{lo=C[a].l;hi=C[b].h}}
    const pad=Math.max((hi-lo)*0.09, Math.abs(hi)*0.004, .01); yMin=lo-pad; yMax=hi+pad;
  }}
  function xPx(i){{return plot.l + ((i-xMin)/(xMax-xMin))*plot.w}}
  function yPx(v){{return plot.t + ((yMax-v)/(yMax-yMin))*plot.h}}
  function pxToIndex(px){{return xMin + ((px-plot.l)/plot.w)*(xMax-xMin)}}
  function pxToPrice(py){{return yMax - ((py-plot.t)/plot.h)*(yMax-yMin)}}
  function nice(v){{
    const av=Math.abs(v); let d=2; if(av<1)d=4; else if(av<10)d=3; else if(av>1000)d=0; return Number(v).toFixed(d);
  }}
  function clear(){{ctx.clearRect(0,0,W,H);ctx.fillStyle='#fff';ctx.fillRect(0,0,W,H)}}
  function line(x1,y1,x2,y2,color='#e5eaf0',width=1,dash=[]){{ctx.save();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();ctx.restore()}}

  function drawGrid(){{
    ctx.font=(W<520?'11':'12')+'px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif'; ctx.textBaseline='middle';
    const steps=5;
    for(let j=0;j<=steps;j++){{
      const py=plot.t+(plot.h*j/steps); const val=yMax-(yMax-yMin)*j/steps;
      line(plot.l,py,plot.l+plot.w,py,'#e7ebf0',1);
      ctx.fillStyle='#6e7887';ctx.textAlign='left';ctx.fillText(nice(val),plot.l+plot.w+8,py);
    }}
    const xticks=W<520?3:6;
    for(let j=0;j<=xticks;j++){{
      const idx=xMin+(xMax-xMin)*j/xticks; const nearest=clamp(Math.round(idx),0,C.length-1);
      const px=xPx(idx); line(px,plot.t,px,plot.t+plot.h,'#f0f2f5',1);
      ctx.fillStyle='#7b8492';ctx.textAlign=j===0?'left':j===xticks?'right':'center';ctx.textBaseline='top';
      const lab=C[nearest].label; ctx.fillText(lab, clamp(px,plot.l+2,plot.l+plot.w-2), plot.t+plot.h+8);
    }}
    line(plot.l+plot.w,plot.t,plot.l+plot.w,plot.t+plot.h,'#cfd6df',1);
  }}

  function drawCandles(){{
    const [a,b]=visibleIndices(); const spacing=plot.w/Math.max(1,(xMax-xMin));
    for(let i=a;i<=b;i++){{
      const c=C[i], px=xPx(i); if(px<plot.l-spacing||px>plot.l+plot.w+spacing)continue;
      const pyH=yPx(c.h),pyL=yPx(c.l),pyO=yPx(c.o),pyC=yPx(c.c);
      const vr=clamp(Number(c.vr)||0,0,1);
      // Hacim şişkinliği belirgin: düşük hacim %25, yüksek hacim %92 bar aralığı.
      const width=clamp(spacing*(0.18 + 0.78*Math.pow(vr,1.40)), 1.2, Math.max(2,spacing*.96));
      ctx.strokeStyle=c.color; ctx.lineWidth=Math.max(1,Math.min(2,spacing*.10)); ctx.beginPath();ctx.moveTo(px,pyH);ctx.lineTo(px,pyL);ctx.stroke();
      const top=Math.min(pyO,pyC), bot=Math.max(pyO,pyC); const bh=Math.max(1.5,bot-top);
      ctx.fillStyle=c.color;ctx.strokeStyle='#19232f';ctx.lineWidth=spacing>6?.75:.35;
      ctx.beginPath();ctx.rect(px-width/2,top,width,bh);ctx.fill();if(width>3)ctx.stroke();
    }}
  }}

  function drawOverlays(){{
    for(const rc of (spec.rects||[])){{
      const x1=xPx(rc.x0),x2=xPx(rc.x1),y1=yPx(rc.y0),y2=yPx(rc.y1);
      ctx.save();ctx.fillStyle=rc.fill||'rgba(245,197,66,.08)';ctx.strokeStyle=rc.color||'#d9a900';ctx.setLineDash(rc.dashed?[5,4]:[]);ctx.lineWidth=rc.width||1.2;ctx.fillRect(x1,Math.min(y1,y2),x2-x1,Math.abs(y2-y1));ctx.strokeRect(x1,Math.min(y1,y2),x2-x1,Math.abs(y2-y1));ctx.restore();
    }}
    for(const ln of (spec.lines||[])){{
      const pts=(ln.points||[]).filter(p=>Number.isFinite(p.i)&&Number.isFinite(p.y)); if(pts.length<2)continue;
      ctx.save();ctx.strokeStyle=ln.color||'#2979ff';ctx.lineWidth=ln.width||2;ctx.setLineDash(ln.dashed?[7,5]:[]);ctx.lineJoin='round';ctx.lineCap='round';ctx.beginPath();
      let started=false;for(const p of pts){{const xx=xPx(p.i),yy=yPx(p.y);if(!started){{ctx.moveTo(xx,yy);started=true}}else ctx.lineTo(xx,yy)}}ctx.stroke();ctx.restore();
    }}
    for(const m of (spec.markers||[])){{
      const x=xPx(m.i),y=yPx(m.y),col=m.color||'#f5b400';ctx.save();ctx.fillStyle=col;ctx.strokeStyle=col;ctx.lineWidth=2;
      if(m.shape==='cross'){{line(x-6,y-6,x+6,y+6,col,2);line(x-6,y+6,x+6,y-6,col,2)}}
      else if(m.shape==='star'){{ctx.beginPath();for(let k=0;k<10;k++){{const a=-Math.PI/2+k*Math.PI/5,r=k%2?4:8,xx=x+Math.cos(a)*r,yy=y+Math.sin(a)*r;k?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)}}ctx.closePath();ctx.fill()}}
      else{{ctx.beginPath();ctx.moveTo(x,y-9);ctx.lineTo(x-8,y+6);ctx.lineTo(x+8,y+6);ctx.closePath();ctx.fill()}}ctx.restore();
    }}
    const lp=Number(spec.lastPrice); if(Number.isFinite(lp)){{
      const py=yPx(lp),col=spec.lastUp?'#16b86c':'#e34b43';line(plot.l,py,plot.l+plot.w,py,col,1,[5,4]);
      const text=nice(lp),tw=ctx.measureText(text).width+14;ctx.fillStyle=col;ctx.fillRect(plot.l+plot.w+2,py-12,Math.min(plot.r-4,tw),24);ctx.fillStyle='#fff';ctx.textAlign='left';ctx.textBaseline='middle';ctx.font='bold 12px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif';ctx.fillText(text,plot.l+plot.w+8,py);
    }}
  }}
  function draw(){{clear();drawGrid();ctx.save();ctx.beginPath();ctx.rect(plot.l,plot.t,plot.w,plot.h);ctx.clip();drawCandles();drawOverlays();ctx.restore();}}

  function resetView(){{xMin=Number(spec.focus?.[0]??Math.max(0,C.length-80));xMax=Number(spec.focus?.[1]??C.length+1);manualY=false;autoY();draw()}}
  function zoomX(factor,anchorPx){{
    const span=xMax-xMin;const minSpan=6,maxSpan=Math.max(20,C.length+18);const newSpan=clamp(span*factor,minSpan,maxSpan);
    const frac=clamp((anchorPx-plot.l)/plot.w,0,1);const anchor=xMin+span*frac;xMin=anchor-newSpan*frac;xMax=anchor+newSpan*(1-frac);
    const overs=8;xMin=clamp(xMin,-overs,C.length-1);xMax=clamp(xMax,xMin+minSpan,C.length+overs);if(!manualY)autoY();draw();
  }}
  function panX(deltaPx,startMin,startMax){{
    const span=startMax-startMin;const di=-(deltaPx/plot.w)*span;let a=startMin+di,b=startMax+di;const overs=8;
    if(a<-overs){{b+=(-overs-a);a=-overs}} if(b>C.length+overs){{a-=(b-(C.length+overs));b=C.length+overs}} xMin=a;xMax=b;if(!manualY)autoY();draw();
  }}
  function scaleYFromDrag(dy,start,anchorPrice,anchorFrac){{
    const oldSpan=start[1]-start[0];const factor=clamp(Math.exp(dy/170),.12,8);const span=oldSpan*factor;
    yMax=anchorPrice+anchorFrac*span;yMin=yMax-span;manualY=true;draw();
  }}

  function posTouch(t){{const r=canvas.getBoundingClientRect();return{{x:t.clientX-r.left,y:t.clientY-r.top}}}}
  canvas.addEventListener('touchstart',e=>{{
    if(e.touches.length===2){{
      const a=posTouch(e.touches[0]),b=posTouch(e.touches[1]);const d=Math.hypot(b.x-a.x,b.y-a.y);if(d>4){{pinch={{d,span:xMax-xMin,min:xMin,max:xMax,cx:(a.x+b.x)/2}};pan=null;axisDrag=null;e.preventDefault();}}
      return;
    }}
    if(e.touches.length===1){{
      const p=posTouch(e.touches[0]);
      if(p.x>=plot.l+plot.w-2){{
        const now=Date.now(); if(now-lastTapAxis<330){{manualY=false;autoY();draw();lastTapAxis=0;e.preventDefault();return}} lastTapAxis=now;
        const span=yMax-yMin,frac=clamp((p.y-plot.t)/plot.h,0,1),anchor=yMax-frac*span;axisDrag={{sy:p.y,range:[yMin,yMax],anchor,frac}};pan=null;e.preventDefault();
      }} else if(p.x>=plot.l&&p.x<=plot.l+plot.w&&p.y>=plot.t&&p.y<=plot.t+plot.h){{pan={{sx:p.x,min:xMin,max:xMax}};axisDrag=null;e.preventDefault();}}
    }}
  }},{{passive:false,capture:true}});
  canvas.addEventListener('touchmove',e=>{{
    if(e.touches.length===2&&pinch){{const a=posTouch(e.touches[0]),b=posTouch(e.touches[1]);const d=Math.hypot(b.x-a.x,b.y-a.y);if(d>4){{const f=clamp(pinch.d/d,.15,6);xMin=pinch.min;xMax=pinch.max;zoomX(f,pinch.cx);e.preventDefault();}}return;}}
    if(e.touches.length===1&&axisDrag){{const p=posTouch(e.touches[0]);scaleYFromDrag(p.y-axisDrag.sy,axisDrag.range,axisDrag.anchor,axisDrag.frac);e.preventDefault();return;}}
    if(e.touches.length===1&&pan){{const p=posTouch(e.touches[0]);panX(p.x-pan.sx,pan.min,pan.max);e.preventDefault();}}
  }},{{passive:false,capture:true}});
  canvas.addEventListener('touchend',e=>{{if(e.touches.length<2)pinch=null;if(e.touches.length===0){{pan=null;axisDrag=null}}}},{{passive:false,capture:true}});
  canvas.addEventListener('touchcancel',()=>{{pinch=null;pan=null;axisDrag=null}},{{passive:false,capture:true}});

  canvas.addEventListener('mousedown',e=>{{
    const r=canvas.getBoundingClientRect(),p={{x:e.clientX-r.left,y:e.clientY-r.top}};
    if(p.x>=plot.l+plot.w-2){{const span=yMax-yMin,frac=clamp((p.y-plot.t)/plot.h,0,1),anchor=yMax-frac*span;mouseAxis={{sy:p.y,range:[yMin,yMax],anchor,frac}};e.preventDefault()}}
    else if(p.x>=plot.l&&p.x<=plot.l+plot.w){{mousePan={{sx:p.x,min:xMin,max:xMax}};e.preventDefault()}}
  }});
  window.addEventListener('mousemove',e=>{{const r=canvas.getBoundingClientRect();if(mouseAxis){{scaleYFromDrag(e.clientY-r.top-mouseAxis.sy,mouseAxis.range,mouseAxis.anchor,mouseAxis.frac)}}else if(mousePan){{panX(e.clientX-r.left-mousePan.sx,mousePan.min,mousePan.max)}}}});
  window.addEventListener('mouseup',()=>{{mousePan=null;mouseAxis=null}});
  canvas.addEventListener('dblclick',e=>{{const r=canvas.getBoundingClientRect(),x=e.clientX-r.left;if(x>=plot.l+plot.w-4){{manualY=false;autoY();draw()}}}});
  canvas.addEventListener('wheel',e=>{{
    const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;e.preventDefault();
    if(x>=plot.l+plot.w-4){{const span=yMax-yMin,frac=clamp((y-plot.t)/plot.h,0,1),anchor=yMax-frac*span;scaleYFromDrag(e.deltaY*.32,[yMin,yMax],anchor,frac)}}else zoomX(e.deltaY>0?1.14:.87,x);
  }},{{passive:false}});

  canvas.addEventListener('click',e=>{{
    if(mousePan||mouseAxis)return;const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;if(x<plot.l||x>plot.l+plot.w||y<plot.t||y>plot.t+plot.h){{tip.style.display='none';return}}
    const i=clamp(Math.round(pxToIndex(x)),0,C.length-1),c=C[i];tip.innerHTML='<b>'+c.label+'</b><br>Aç '+nice(c.o)+' · Yük '+nice(c.h)+' · Düş '+nice(c.l)+' · Kapanış '+nice(c.c)+'<br>Hacim '+Math.round(c.v).toLocaleString('tr-TR');tip.style.display='block';
  }});

  resetBtn.addEventListener('click',e=>{{e.stopPropagation();resetView()}});
  fsBtn.addEventListener('click',async e=>{{e.stopPropagation();try{{if(!document.fullscreenElement)await host.requestFullscreen();else await document.exitFullscreen()}}catch(_e){{}}}});
  document.addEventListener('fullscreenchange',()=>setTimeout(resize,50));
  new ResizeObserver(()=>resize()).observe(host);
  window.addEventListener('orientationchange',()=>setTimeout(resize,120));

  // Test/debug hooks; UI'da görünmez.
  window.__bistChartDebug={{getRanges:()=>({{xMin,xMax,yMin,yMax,manualY,W,H,plot}}),reset:resetView,volumeWidths:()=>C.map(c=>c.vr)}};
  resetView();
}})();
</script>
"""
    components.html(html, height=max(height, 720) + 4, scrolling=False)


def render_vwap_chart(sym: str, r: Dict[str, Any], key: Optional[str] = None) -> None:
    df = r["df"].reset_index(drop=True)
    n = len(df)
    cross_idx = int(r.get("cross_idx", max(0, n - 1)))
    spec = _base_spec(df, str(r.get("period", "")), max(0, cross_idx - 55), n - 1, str(r.get("currency", "TRY")))
    for info in r.get("chain", []):
        lvl = int(info.get("level", 0) or 0)
        _add_line(spec, _series_points(info.get("vwap")), VWAP_COLORS.get(lvl, "#7b8492"), 2.0,
                  dashed=(lvl != int(r.get("level", 0) or 0)))
    if 0 <= cross_idx < n:
        _add_marker(spec, cross_idx, float(df["Close"].iloc[cross_idx]), "#d99b16", "star")
    tr = r.get("trendline") or {}
    if tr.get("matched") and tr.get("line"):
        ln = tr["line"]; s=_finite(ln.get("slope")); b=_finite(ln.get("intercept")); x1=int(ln.get("x1",0)); x2=int(ln.get("x2",0)); cx=int(tr.get("cross_idx",x2))
        if s is not None and b is not None:
            _add_line(spec,[{"i":x1,"y":s*x1+b},{"i":x2,"y":s*x2+b}],"#ff5f5f",2.4,False)
            _add_line(spec,[{"i":x2,"y":s*x2+b},{"i":cx,"y":s*cx+b}],"#ff5f5f",1.8,True)
            if 0<=cx<n:_add_marker(spec,cx,float(df["Close"].iloc[cx]),"#ff5f5f","triangle")
    _render_canvas_chart(spec, key=key, height=680)


def render_triangle_chart(sym: str, r: Dict[str, Any], key: Optional[str] = None) -> None:
    df = r["df"].reset_index(drop=True); n=len(df)
    upper,lower=r["upper"],r["lower"]
    start=min(int(upper.get("x1",0)),int(lower.get("x1",0)))
    spec=_base_spec(df,str(r.get("period","")),max(0,start-8),n-1,"TRY")
    apex_x=_finite(r.get("apex_x"),n-1) or n-1; draw_to=min(float(apex_x), n-1+8)
    for ln,col in ((upper,"#ff5f5f"),(lower,"#18a864")):
        s=_finite(ln.get("slope"));b=_finite(ln.get("intercept"));x1=int(ln.get("x1",0));x2=int(ln.get("x2",0))
        if s is not None and b is not None:
            _add_line(spec,[{"i":x1,"y":s*x1+b},{"i":x2,"y":s*x2+b}],col,2.4,False)
            _add_line(spec,[{"i":x2,"y":s*x2+b},{"i":draw_to,"y":s*draw_to+b}],col,1.8,True)
    apy=_finite(r.get("apex_y"));
    if apy is not None:_add_marker(spec,int(round(apex_x)),apy,"#d99b16","cross")
    _render_canvas_chart(spec,key=key,height=680)


def render_trendline_chart(sym: str, r: Dict[str, Any], key: Optional[str] = None) -> None:
    df=r["df"].reset_index(drop=True); n=len(df); ln=r["line"]
    x1=int(ln.get("x1",0));x2=int(ln.get("x2",0));cross=int(r.get("cross_idx",x2))
    spec=_base_spec(df,str(r.get("period","")),max(0,x1-8),n-1,"TRY")
    s=_finite(ln.get("slope"));b=_finite(ln.get("intercept"))
    if s is not None and b is not None:
        _add_line(spec,[{"i":x1,"y":s*x1+b},{"i":x2,"y":s*x2+b}],"#ff5f5f",2.5,False)
        _add_line(spec,[{"i":x2,"y":s*x2+b},{"i":cross,"y":s*cross+b}],"#ff5f5f",1.8,True)
    if 0<=cross<n:_add_marker(spec,cross,float(df["Close"].iloc[cross]),"#ff5f5f","triangle")
    _render_canvas_chart(spec,key=key,height=680)


def render_alternation_chart(sym: str, r: Dict[str, Any], key: Optional[str] = None) -> None:
    df=r["df"].reset_index(drop=True); n=len(df);start=int(r.get("start_idx",max(0,n-10)));end=int(r.get("end_idx",n-1))
    spec=_base_spec(df,str(r.get("period","")),max(0,start-20),n-1,"TRY")
    sub=df.iloc[max(0,start):min(n,end+1)]
    if not sub.empty:
        lo=float(sub["Low"].min());hi=float(sub["High"].max());pad=max((hi-lo)*.06,abs(hi)*.004,.01)
        spec["rects"].append({"x0":start-.5,"x1":end+.5,"y0":lo-pad,"y1":hi+pad,"color":"#d5a800","fill":"rgba(245,197,66,.07)","dashed":True})
        pts=[{"i":i,"y":float(df["Close"].iloc[i])} for i in range(max(0,start),min(n,end+1))]
        _add_line(spec,pts,"#d5a800",1.7,True)
    _render_canvas_chart(spec,key=key,height=680)
