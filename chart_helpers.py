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
    """TradingView benzeri özel Canvas grafik motoru.

    Etkileşim bölgeleri:
    - Grafik gövdesi: X+Y serbest pan.
    - Alt tarih ekseni: yatay ölçekleme.
    - Sağ fiyat ekseni: dikey ölçekleme.
    - İki parmak pinch: X+Y birlikte zoom.
    """
    raw_key = str(key or "chart")
    uid = "bistcanvas_" + hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:12]
    payload = json.dumps(spec, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    mobile_height = max(height, 720)

    html = r"""
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<div id="__UID___wrap" class="bist-wrap">
  <div id="__UID___chart" class="bist-chart" aria-label="Etkileşimli fiyat grafiği">
    <canvas id="__UID___canvas"></canvas>
    <div id="__UID___tip" class="bist-tip"></div>
    <button id="__UID___reset" class="chart-btn reset-btn" type="button" aria-label="Grafiği sıfırla">↺</button>
    <button id="__UID___fs" class="chart-btn fs-btn" type="button" aria-label="Tam ekran">⛶</button>
  </div>
</div>
<style>
  html,body{margin:0;padding:0;background:#fff;overflow:hidden;-webkit-text-size-adjust:100%;}
  .bist-wrap{width:100%;height:__HEIGHT__px;background:#fff;}
  .bist-chart{position:relative;width:100%;height:100%;background:#fff;overflow:hidden;touch-action:none;overscroll-behavior:contain;user-select:none;-webkit-user-select:none;}
  .bist-chart canvas{position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none;-webkit-user-select:none;user-select:none;}
  .chart-btn{position:absolute;top:8px;z-index:5;width:36px;height:36px;border:1px solid #d6dde7;border-radius:9px;background:rgba(255,255,255,.92);color:#657285;font-size:21px;line-height:32px;padding:0;}
  .reset-btn{right:50px}.fs-btn{right:8px}
  .bist-tip{position:absolute;display:none;z-index:6;pointer-events:none;left:8px;top:8px;max-width:70%;background:rgba(255,255,255,.94);border:1px solid #d6dde7;border-radius:8px;padding:6px 8px;font:12px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1b2638;box-shadow:0 2px 8px rgba(0,0,0,.08)}
  @media(max-width:760px){
    .bist-wrap{height:__MOBILE_HEIGHT__px}
    .chart-btn{width:42px;height:42px;font-size:23px}
    .reset-btn{right:58px}
  }
  .bist-chart:fullscreen{width:100vw!important;height:100vh!important;background:#fff}
</style>
<script>
(() => {
  const spec = __PAYLOAD__;
  const host = document.getElementById('__UID___chart');
  const canvas = document.getElementById('__UID___canvas');
  const tip = document.getElementById('__UID___tip');
  const resetBtn = document.getElementById('__UID___reset');
  const fsBtn = document.getElementById('__UID___fs');
  const ctx = canvas.getContext('2d');
  const C = spec.candles || [];
  if (!C.length) { ctx.font='14px sans-serif'; ctx.fillStyle='#6b7280'; ctx.fillText('Grafik verisi yok',20,30); return; }

  let dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  let W=0,H=0, plot={l:8,r:76,t:8,b:40,w:0,h:0};
  let xMin = Number(spec.focus?.[0] ?? Math.max(0,C.length-80));
  let xMax = Number(spec.focus?.[1] ?? C.length+1);
  let yMin=0, yMax=1, manualY=false;
  let gesture=null, pinch=null, mouseGesture=null;
  let lastTapAxis=0, lastTapTime=0;
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));

  function resize() {
    const r=host.getBoundingClientRect(); W=Math.max(280,r.width); H=Math.max(360,r.height);
    dpr=Math.max(1,Math.min(3,window.devicePixelRatio||1));
    canvas.width=Math.round(W*dpr); canvas.height=Math.round(H*dpr);
    canvas.style.width=W+'px'; canvas.style.height=H+'px';
    ctx.setTransform(dpr,0,0,dpr,0,0);
    plot.r = W < 520 ? 72 : 78;
    plot.l = W < 520 ? 4 : 8;
    plot.t = 8;
    plot.b = W < 520 ? 46 : 42;
    plot.w=Math.max(80,W-plot.l-plot.r);
    plot.h=Math.max(80,H-plot.t-plot.b);
    if(!manualY) autoY();
    draw();
  }

  function visibleIndices() {
    const a=Math.max(0,Math.floor(xMin)-2), b=Math.min(C.length-1,Math.ceil(xMax)+2);
    return [a,b];
  }
  function autoY() {
    const [a,b]=visibleIndices(); let lo=Infinity,hi=-Infinity;
    for(let i=a;i<=b;i++){ lo=Math.min(lo,C[i].l); hi=Math.max(hi,C[i].h); }
    if(!Number.isFinite(lo)||!Number.isFinite(hi)||hi<=lo){lo=C[a].l;hi=C[b].h;}
    const pad=Math.max((hi-lo)*0.09, Math.abs(hi)*0.004, .01);
    yMin=lo-pad; yMax=hi+pad;
  }
  function xPx(i){return plot.l + ((i-xMin)/(xMax-xMin))*plot.w;}
  function yPx(v){return plot.t + ((yMax-v)/(yMax-yMin))*plot.h;}
  function pxToIndex(px){return xMin + ((px-plot.l)/plot.w)*(xMax-xMin);}
  function pxToPrice(py){return yMax - ((py-plot.t)/plot.h)*(yMax-yMin);}
  function nice(v){
    const av=Math.abs(v); let d=2;
    if(av<1)d=4; else if(av<10)d=3; else if(av>1000)d=0;
    return Number(v).toFixed(d);
  }
  function clear(){ctx.clearRect(0,0,W,H);ctx.fillStyle='#fff';ctx.fillRect(0,0,W,H);}
  function line(x1,y1,x2,y2,color='#e5eaf0',width=1,dash=[]){
    ctx.save();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();ctx.restore();
  }

  function drawGrid(){
    ctx.font=(W<520?'11':'12')+'px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif';
    ctx.textBaseline='middle';
    const steps=5;
    for(let j=0;j<=steps;j++){
      const py=plot.t+(plot.h*j/steps), val=yMax-(yMax-yMin)*j/steps;
      line(plot.l,py,plot.l+plot.w,py,'#e7ebf0',1);
      ctx.fillStyle='#6e7887';ctx.textAlign='left';ctx.fillText(nice(val),plot.l+plot.w+8,py);
    }
    const xticks=W<520?3:6;
    for(let j=0;j<=xticks;j++){
      const idx=xMin+(xMax-xMin)*j/xticks, nearest=clamp(Math.round(idx),0,C.length-1), px=xPx(idx);
      line(px,plot.t,px,plot.t+plot.h,'#f0f2f5',1);
      ctx.fillStyle='#7b8492';ctx.textAlign=j===0?'left':j===xticks?'right':'center';ctx.textBaseline='top';
      ctx.fillText(C[nearest].label,clamp(px,plot.l+2,plot.l+plot.w-2),plot.t+plot.h+11);
    }
    // Sağ fiyat sütunu ve alt tarih satırı, TradingView benzeri ayrı gesture alanlarıdır.
    line(plot.l+plot.w,plot.t,plot.l+plot.w,H,'#cbd3dd',1);
    line(plot.l,plot.t+plot.h,plot.l+plot.w,plot.t+plot.h,'#cbd3dd',1);
  }

  function drawCandles(){
    const [a,b]=visibleIndices(), spacing=plot.w/Math.max(1,(xMax-xMin));
    for(let i=a;i<=b;i++){
      const c=C[i], px=xPx(i); if(px<plot.l-spacing||px>plot.l+plot.w+spacing)continue;
      const pyH=yPx(c.h),pyL=yPx(c.l),pyO=yPx(c.o),pyC=yPx(c.c),vr=clamp(Number(c.vr)||0,0,1);
      // Ayrı hacim paneli yok: normal fiyat mumunun GÖVDESİ hacme göre şişer.
      const width=clamp(spacing*(0.15 + 0.82*Math.pow(vr,1.22)),1.2,Math.max(2,spacing*.98));
      ctx.strokeStyle=c.color;ctx.lineWidth=Math.max(1,Math.min(2,spacing*.10));ctx.beginPath();ctx.moveTo(px,pyH);ctx.lineTo(px,pyL);ctx.stroke();
      const top=Math.min(pyO,pyC),bot=Math.max(pyO,pyC),bh=Math.max(1.5,bot-top);
      ctx.fillStyle=c.color;ctx.strokeStyle='#19232f';ctx.lineWidth=spacing>6?.75:.35;
      ctx.beginPath();ctx.rect(px-width/2,top,width,bh);ctx.fill();if(width>3)ctx.stroke();
    }
  }

  function drawOverlays(){
    for(const rc of (spec.rects||[])){
      const x1=xPx(rc.x0),x2=xPx(rc.x1),y1=yPx(rc.y0),y2=yPx(rc.y1);
      ctx.save();ctx.fillStyle=rc.fill||'rgba(245,197,66,.08)';ctx.strokeStyle=rc.color||'#d9a900';ctx.setLineDash(rc.dashed?[5,4]:[]);ctx.lineWidth=rc.width||1.2;
      ctx.fillRect(x1,Math.min(y1,y2),x2-x1,Math.abs(y2-y1));ctx.strokeRect(x1,Math.min(y1,y2),x2-x1,Math.abs(y2-y1));ctx.restore();
    }
    for(const ln of (spec.lines||[])){
      const pts=(ln.points||[]).filter(p=>Number.isFinite(p.i)&&Number.isFinite(p.y)); if(pts.length<2)continue;
      ctx.save();ctx.strokeStyle=ln.color||'#2979ff';ctx.lineWidth=ln.width||2;ctx.setLineDash(ln.dashed?[7,5]:[]);ctx.lineJoin='round';ctx.lineCap='round';ctx.beginPath();
      let started=false;for(const p of pts){const xx=xPx(p.i),yy=yPx(p.y);if(!started){ctx.moveTo(xx,yy);started=true;}else ctx.lineTo(xx,yy);}ctx.stroke();ctx.restore();
    }
    for(const m of (spec.markers||[])){
      const x=xPx(m.i),y=yPx(m.y),col=m.color||'#f5b400';ctx.save();ctx.fillStyle=col;ctx.strokeStyle=col;ctx.lineWidth=2;
      if(m.shape==='cross'){line(x-6,y-6,x+6,y+6,col,2);line(x-6,y+6,x+6,y-6,col,2);}
      else if(m.shape==='star'){ctx.beginPath();for(let k=0;k<10;k++){const a=-Math.PI/2+k*Math.PI/5,r=k%2?4:8,xx=x+Math.cos(a)*r,yy=y+Math.sin(a)*r;k?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy);}ctx.closePath();ctx.fill();}
      else{ctx.beginPath();ctx.moveTo(x,y-9);ctx.lineTo(x-8,y+6);ctx.lineTo(x+8,y+6);ctx.closePath();ctx.fill();}
      ctx.restore();
    }
    const lp=Number(spec.lastPrice);
    if(Number.isFinite(lp)){
      const py=yPx(lp),col=spec.lastUp?'#16b86c':'#e34b43';line(plot.l,py,plot.l+plot.w,py,col,1,[5,4]);
      const text=nice(lp),tw=ctx.measureText(text).width+14;ctx.fillStyle=col;ctx.fillRect(plot.l+plot.w+2,py-12,Math.min(plot.r-4,tw),24);
      ctx.fillStyle='#fff';ctx.textAlign='left';ctx.textBaseline='middle';ctx.font='bold 12px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif';ctx.fillText(text,plot.l+plot.w+8,py);
    }
  }
  function draw(){clear();drawGrid();ctx.save();ctx.beginPath();ctx.rect(plot.l,plot.t,plot.w,plot.h);ctx.clip();drawCandles();drawOverlays();ctx.restore();}

  function resetView(){
    xMin=Number(spec.focus?.[0]??Math.max(0,C.length-80));xMax=Number(spec.focus?.[1]??C.length+1);
    manualY=false;autoY();draw();
  }
  function clampXRange(a,b){
    const minSpan=6,maxSpan=Math.max(20,C.length+18),overs=8;
    let span=clamp(b-a,minSpan,maxSpan),mid=(a+b)/2;a=mid-span/2;b=mid+span/2;
    if(a<-overs){b+=(-overs-a);a=-overs;} if(b>C.length+overs){a-=(b-(C.length+overs));b=C.length+overs;}
    return [a,b];
  }
  function setXScale(span,anchorIndex,anchorFrac){
    const minSpan=6,maxSpan=Math.max(20,C.length+18);span=clamp(span,minSpan,maxSpan);
    let a=anchorIndex-span*anchorFrac,b=anchorIndex+span*(1-anchorFrac);[a,b]=clampXRange(a,b);xMin=a;xMax=b;
  }
  function setYScale(span,anchorPrice,anchorFrac){
    const floor=Math.max(Math.abs(anchorPrice)*1e-6,1e-6);span=Math.max(floor,span);
    yMax=anchorPrice+anchorFrac*span;yMin=yMax-span;manualY=true;
  }
  function pan2D(dx,dy,start){
    const xSpan=start.xMax-start.xMin,di=-(dx/plot.w)*xSpan;
    let [a,b]=clampXRange(start.xMin+di,start.xMax+di);xMin=a;xMax=b;
    const ySpan=start.yMax-start.yMin,dp=(dy/plot.h)*ySpan;
    yMin=start.yMin+dp;yMax=start.yMax+dp;manualY=true;draw();
  }
  function scalePriceAxis(dy,start){
    // Sağ fiyat sütunu: yukarı sürükle => dikey zoom-in, aşağı => zoom-out.
    const factor=clamp(Math.exp(dy/165),.08,12),span=(start.yMax-start.yMin)*factor;
    setYScale(span,start.anchorPrice,start.anchorFrac);draw();
  }
  function scaleTimeAxis(dx,start){
    // Alt tarih satırı: sağa sürükle => mumlar genişler, sola => daha fazla mum görünür.
    const factor=clamp(Math.exp(-dx/190),.10,10),span=(start.xMax-start.xMin)*factor;
    setXScale(span,start.anchorIndex,start.anchorFrac);
    if(!manualY)autoY();draw();
  }
  function applyPinch(a,b,start){
    const dist=Math.max(8,Math.hypot(b.x-a.x,b.y-a.y)),factor=clamp(start.dist/dist,.12,8);
    const mx=(a.x+b.x)/2,my=(a.y+b.y)/2,fracX=clamp((mx-plot.l)/plot.w,0,1),fracY=clamp((my-plot.t)/plot.h,0,1);
    setXScale((start.xMax-start.xMin)*factor,start.anchorIndex,fracX);
    setYScale((start.yMax-start.yMin)*factor,start.anchorPrice,fracY);
    draw();
  }

  function posTouch(t){const r=canvas.getBoundingClientRect();return{x:t.clientX-r.left,y:t.clientY-r.top};}
  function zoneFor(p){
    if(p.x>=plot.l+plot.w) return 'price';
    if(p.y>=plot.t+plot.h && p.x>=plot.l && p.x<plot.l+plot.w) return 'time';
    if(p.x>=plot.l && p.x<plot.l+plot.w && p.y>=plot.t && p.y<plot.t+plot.h) return 'body';
    return 'none';
  }

  canvas.addEventListener('touchstart',e=>{
    if(e.touches.length===2){
      const a=posTouch(e.touches[0]),b=posTouch(e.touches[1]),mx=(a.x+b.x)/2,my=(a.y+b.y)/2;
      pinch={dist:Math.max(8,Math.hypot(b.x-a.x,b.y-a.y)),xMin,xMax,yMin,yMax,anchorIndex:pxToIndex(mx),anchorPrice:pxToPrice(my)};
      gesture=null;e.preventDefault();return;
    }
    if(e.touches.length!==1)return;
    const p=posTouch(e.touches[0]),zone=zoneFor(p);
    if(zone==='price'){
      const now=Date.now();
      if(now-lastTapAxis<330){manualY=false;autoY();draw();lastTapAxis=0;e.preventDefault();return;}
      lastTapAxis=now;
      const span=yMax-yMin,frac=clamp((p.y-plot.t)/plot.h,0,1),anchor=yMax-frac*span;
      gesture={type:'price',sx:p.x,sy:p.y,yMin,yMax,anchorPrice:anchor,anchorFrac:frac};
    } else if(zone==='time'){
      const now=Date.now();
      if(now-lastTapTime<330){xMin=Number(spec.focus?.[0]??Math.max(0,C.length-80));xMax=Number(spec.focus?.[1]??C.length+1);if(!manualY)autoY();draw();lastTapTime=0;e.preventDefault();return;}
      lastTapTime=now;
      const frac=clamp((p.x-plot.l)/plot.w,0,1);
      gesture={type:'time',sx:p.x,sy:p.y,xMin,xMax,anchorIndex:pxToIndex(p.x),anchorFrac:frac};
    } else if(zone==='body'){
      gesture={type:'body',sx:p.x,sy:p.y,xMin,xMax,yMin,yMax};
    } else gesture=null;
    if(gesture)e.preventDefault();
  },{passive:false,capture:true});

  canvas.addEventListener('touchmove',e=>{
    if(e.touches.length===2&&pinch){const a=posTouch(e.touches[0]),b=posTouch(e.touches[1]);applyPinch(a,b,pinch);e.preventDefault();return;}
    if(e.touches.length!==1||!gesture)return;
    const p=posTouch(e.touches[0]);
    if(gesture.type==='body')pan2D(p.x-gesture.sx,p.y-gesture.sy,gesture);
    else if(gesture.type==='price')scalePriceAxis(p.y-gesture.sy,gesture);
    else if(gesture.type==='time')scaleTimeAxis(p.x-gesture.sx,gesture);
    e.preventDefault();
  },{passive:false,capture:true});
  canvas.addEventListener('touchend',e=>{if(e.touches.length<2)pinch=null;if(e.touches.length===0)gesture=null;},{passive:false,capture:true});
  canvas.addEventListener('touchcancel',()=>{pinch=null;gesture=null;},{passive:false,capture:true});

  canvas.addEventListener('mousedown',e=>{
    const r=canvas.getBoundingClientRect(),p={x:e.clientX-r.left,y:e.clientY-r.top},zone=zoneFor(p);
    if(zone==='price'){
      const span=yMax-yMin,frac=clamp((p.y-plot.t)/plot.h,0,1),anchor=yMax-frac*span;
      mouseGesture={type:'price',sx:p.x,sy:p.y,yMin,yMax,anchorPrice:anchor,anchorFrac:frac};
    } else if(zone==='time'){
      const frac=clamp((p.x-plot.l)/plot.w,0,1);mouseGesture={type:'time',sx:p.x,sy:p.y,xMin,xMax,anchorIndex:pxToIndex(p.x),anchorFrac:frac};
    } else if(zone==='body') mouseGesture={type:'body',sx:p.x,sy:p.y,xMin,xMax,yMin,yMax};
    if(mouseGesture)e.preventDefault();
  });
  window.addEventListener('mousemove',e=>{
    if(!mouseGesture)return;const r=canvas.getBoundingClientRect(),p={x:e.clientX-r.left,y:e.clientY-r.top};
    if(mouseGesture.type==='body')pan2D(p.x-mouseGesture.sx,p.y-mouseGesture.sy,mouseGesture);
    else if(mouseGesture.type==='price')scalePriceAxis(p.y-mouseGesture.sy,mouseGesture);
    else if(mouseGesture.type==='time')scaleTimeAxis(p.x-mouseGesture.sx,mouseGesture);
  });
  window.addEventListener('mouseup',()=>{mouseGesture=null;});
  canvas.addEventListener('dblclick',e=>{
    const r=canvas.getBoundingClientRect(),p={x:e.clientX-r.left,y:e.clientY-r.top},zone=zoneFor(p);
    if(zone==='price'){manualY=false;autoY();draw();}
    else if(zone==='time'){xMin=Number(spec.focus?.[0]??Math.max(0,C.length-80));xMax=Number(spec.focus?.[1]??C.length+1);if(!manualY)autoY();draw();}
  });
  canvas.addEventListener('wheel',e=>{
    const r=canvas.getBoundingClientRect(),p={x:e.clientX-r.left,y:e.clientY-r.top},zone=zoneFor(p);e.preventDefault();
    if(zone==='price'){
      const span=yMax-yMin,frac=clamp((p.y-plot.t)/plot.h,0,1),anchor=yMax-frac*span;
      scalePriceAxis(e.deltaY*.30,{yMin,yMax,anchorPrice:anchor,anchorFrac:frac});
    } else {
      const frac=clamp((p.x-plot.l)/plot.w,0,1),anchor=pxToIndex(p.x),factor=e.deltaY>0?1.14:.87;
      setXScale((xMax-xMin)*factor,anchor,frac);if(!manualY)autoY();draw();
    }
  },{passive:false});

  canvas.addEventListener('click',e=>{
    const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
    if(x<plot.l||x>plot.l+plot.w||y<plot.t||y>plot.t+plot.h){tip.style.display='none';return;}
    const i=clamp(Math.round(pxToIndex(x)),0,C.length-1),c=C[i];
    tip.innerHTML='<b>'+c.label+'</b><br>Aç '+nice(c.o)+' · Yük '+nice(c.h)+' · Düş '+nice(c.l)+' · Kapanış '+nice(c.c)+'<br>Hacim '+Math.round(c.v).toLocaleString('tr-TR');
    tip.style.display='block';
  });

  resetBtn.addEventListener('click',e=>{e.stopPropagation();resetView();});
  fsBtn.addEventListener('click',async e=>{e.stopPropagation();try{if(!document.fullscreenElement)await host.requestFullscreen();else await document.exitFullscreen();}catch(_e){}});
  document.addEventListener('fullscreenchange',()=>setTimeout(resize,50));
  new ResizeObserver(()=>resize()).observe(host);
  window.addEventListener('orientationchange',()=>setTimeout(resize,120));

  // UI'da görünmeyen test/debug kancaları.
  window.__bistChartDebug={
    getRanges:()=>({xMin,xMax,yMin,yMax,manualY,W,H,plot}),
    reset:resetView,
    volumeWidths:()=>C.map(c=>c.vr),
    simulateBodyPan:(dx,dy)=>pan2D(dx,dy,{xMin,xMax,yMin,yMax}),
    simulatePriceScale:(dy)=>{const span=yMax-yMin;scalePriceAxis(dy,{yMin,yMax,anchorPrice:(yMin+yMax)/2,anchorFrac:.5});},
    simulateTimeScale:(dx)=>scaleTimeAxis(dx,{xMin,xMax,anchorIndex:(xMin+xMax)/2,anchorFrac:.5})
  };
  resetView();
})();
</script>
"""
    html = (html.replace("__UID__", uid)
                .replace("__HEIGHT__", str(int(height)))
                .replace("__MOBILE_HEIGHT__", str(int(mobile_height)))
                .replace("__PAYLOAD__", payload))
    components.html(html, height=mobile_height + 4, scrolling=False)

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
