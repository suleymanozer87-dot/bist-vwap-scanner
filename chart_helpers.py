# -*- coding: utf-8 -*-
"""
chart_helpers.py — V5.3 TradingView gesture kontrollü grafik motoru.

Amaç:
- Grafik etkileşimini el yapımı canvas gesture kodundan çıkarıp TradingView'in
  açık kaynak Lightweight Charts motoruna vermek.
- Mobilde grafik gövdesinde X+Y serbest pan, iki parmak pinch zoom,
  sağ fiyat ekseninde dikey scale ve alt zaman ekseninde yatay scale.
- Ayrı hacim paneli YOK.
- Normal fiyat mumlarının gövde genişliği hacim yüzdelik sırasına göre değişir:
  yüksek hacim = daha geniş/şişkin gövde, düşük hacim = daha ince gövde.

Tarama / sinyal mantığı bu dosyada değildir.
"""

from __future__ import annotations

import json
import math
import hashlib
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit.components.v1 as components

VWAP_COLORS = {1: "#089981", 2: "#2962FF", 3: "#D89B16"}
CURRENCY_AXIS_LABELS = {"TRY": "TL", "USD": "$", "EUR": "€"}

# TradingView'e yakın standart mum renkleri. Hacim renk ile değil gövde eniyle anlatılır.
UP_COLOR = "#089981"
DOWN_COLOR = "#F23645"

LWC_CDN = "https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js"


def _finite(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _volume_rank(volume: pd.Series) -> List[float]:
    """0..1 hacim yüzdelik sırası; aşırı değerler gövde farkını ezmesin."""
    v = pd.to_numeric(volume, errors="coerce").fillna(0.0)
    if len(v) <= 1:
        return [1.0] * len(v)
    r = v.rank(pct=True, method="average").clip(0.0, 1.0)
    return [float(x) for x in r]


def _date_label(ts: Any, intraday: bool) -> str:
    try:
        p = pd.Timestamp(ts)
        return p.strftime("%d.%m %H:%M" if intraday else "%d.%m.%Y")
    except Exception:
        return str(ts)


def _chart_time(ts: Any, fallback_i: int, intraday: bool) -> int:
    """Lightweight Charts UTCTimestamp.

    Veri çekirdeğinde BIST saatleri çoğunlukla timezone-naive tutuluyor. Naive zamanı
    Europe/Istanbul kabul ederek epoch'a çeviriyoruz; böylece mobil eksende saat 3 saat
    kaymıyor. Tarih okunamazsa düzeni bozmamak için monoton sentetik zaman kullanılır.
    """
    try:
        p = pd.Timestamp(ts)
        if p.tzinfo is None:
            p = p.tz_localize("Europe/Istanbul", ambiguous="NaT", nonexistent="shift_forward")
            if pd.isna(p):
                raise ValueError("ambiguous time")
        else:
            p = p.tz_convert("Europe/Istanbul")
        return int(p.timestamp())
    except Exception:
        step = 3600 if intraday else 86400
        return int(1_700_000_000 + fallback_i * step)


def _candles_from_df(df: pd.DataFrame, period: str = "") -> List[Dict[str, Any]]:
    intraday = str(period) in ("1h", "4h")
    ranks = _volume_rank(df.get("Volume", pd.Series([0] * len(df))))
    out: List[Dict[str, Any]] = []
    prev_time: Optional[int] = None
    for i, row in df.reset_index(drop=True).iterrows():
        o = _finite(row.get("Open")); h = _finite(row.get("High")); l = _finite(row.get("Low")); c = _finite(row.get("Close"))
        if None in (o, h, l, c):
            continue
        t = _chart_time(row.get("Date", i), i, intraday)
        # Lightweight Charts times must be strictly ascending in setData.
        if prev_time is not None and t <= prev_time:
            t = prev_time + (3600 if intraday else 86400)
        prev_time = t
        vol = _finite(row.get("Volume"), 0.0) or 0.0
        rank = ranks[i] if i < len(ranks) else 0.5
        up = c >= o
        out.append({
            "i": int(i),
            "time": int(t),
            "label": _date_label(row.get("Date", i), intraday),
            "open": o, "high": h, "low": l, "close": c,
            "volume": vol,
            "vr": round(float(rank), 6),
            "color": UP_COLOR if up else DOWN_COLOR,
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
    if focus_end - focus_start + 1 < 45:
        focus_start = max(0, focus_end - 44)
    if focus_end - focus_start + 1 > 105:
        focus_start = max(0, focus_end - 104)
    return {
        "candles": candles,
        "lines": [],
        "markers": [],
        "rects": [],
        "focus": [focus_start, focus_end + 1.5],
        "currency": CURRENCY_AXIS_LABELS.get(currency, currency),
        "period": str(period or ""),
        "lastPrice": candles[-1]["close"],
        "lastUp": bool(candles[-1]["close"] >= candles[-1]["open"]),
    }


def _add_line(spec: Dict[str, Any], points: List[Dict[str, float]], color: str,
              width: float = 2.0, dashed: bool = False) -> None:
    if len(points) >= 2:
        spec["lines"].append({"points": points, "color": color, "width": width, "dashed": dashed})


def _add_marker(spec: Dict[str, Any], i: int, y: float, color: str, shape: str = "triangle") -> None:
    yy = _finite(y)
    if yy is not None:
        spec["markers"].append({"i": int(i), "y": yy, "color": color, "shape": shape})


def _render_lwc_chart(spec: Dict[str, Any], key: Optional[str] = None, height: int = 650) -> None:
    """TradingView Lightweight Charts tabanlı grafik.

    Etkileşimi library'nin native gesture sistemi yönetir:
    - gövde: horizontal + vertical touch drag
    - pinch: zoom
    - right price scale drag: vertical scale
    - bottom time scale drag: horizontal scale
    """
    raw_key = str(key or "chart")
    uid = "bistlwc_" + hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:12]
    payload = json.dumps(spec, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    mobile_height = max(int(height), 650)

    html = r"""
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<div id="__UID___root" class="lwc-root">
  <div id="__UID___chart" class="lwc-chart"></div>
  <div id="__UID___loading" class="lwc-loading">Grafik hazırlanıyor…</div>
  <div id="__UID___error" class="lwc-error" hidden></div>
</div>
<style>
  html,body{margin:0;padding:0;background:#fff;overflow:hidden;-webkit-text-size-adjust:100%;}
  .lwc-root{position:relative;width:100%;height:__HEIGHT__px;background:#fff;overflow:hidden;}
  .lwc-chart{position:absolute;inset:0;background:#fff;touch-action:none;overscroll-behavior:contain;}
  .lwc-loading,.lwc-error{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:24px;text-align:center;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#6b7280;background:#fff;z-index:2;}
  .lwc-error{color:#b42318;}
  @media(max-width:760px){.lwc-root{height:__MOBILE_HEIGHT__px}}
</style>
<script>
(() => {
  const ROOT='__UID__';
  const spec=__PAYLOAD__;
  const container=document.getElementById(ROOT+'_chart');
  const loading=document.getElementById(ROOT+'_loading');
  const errorBox=document.getElementById(ROOT+'_error');
  const cdn='__LWC_CDN__';

  function fail(msg){
    loading.style.display='none';
    errorBox.hidden=false;
    errorBox.textContent=msg;
  }

  function loadLibrary(){
    return new Promise((resolve,reject)=>{
      if(window.LightweightCharts){resolve(window.LightweightCharts);return;}
      const s=document.createElement('script');s.src=cdn;s.async=true;
      s.onload=()=>window.LightweightCharts?resolve(window.LightweightCharts):reject(new Error('Kütüphane yüklendi ancak API bulunamadı'));
      s.onerror=()=>reject(new Error('TradingView grafik kütüphanesi indirilemedi'));
      document.head.appendChild(s);
    });
  }

  function precisionFor(v){const a=Math.abs(Number(v)||0);return a<1?4:(a<10?3:2);}

  class VolumeCandleRenderer{
    constructor(){this.data=null;}
    setData(data){this.data=data;}
    draw(target,priceConverter){
      const d=this.data;if(!d)return;
      target.useMediaCoordinateSpace(scope=>{
        const ctx=scope.context;
        const spacing=Math.max(1,Number(d.barSpacing)||6);
        for(const bar of d.bars){
          if(!Number.isFinite(bar.x))continue;
          const c=bar.originalData;if(!c)continue;
          const yh=priceConverter(c.high),yl=priceConverter(c.low),yo=priceConverter(c.open),yc=priceConverter(c.close);
          if(![yh,yl,yo,yc].every(Number.isFinite))continue;
          const vr=Math.max(0,Math.min(1,Number(c.vr)||0));
          // Hacme göre belirgin gövde genişliği. Minimum %18, maksimum %94 bar aralığı.
          const bodyW=Math.max(1,Math.min(spacing*.94,spacing*(.18+.76*Math.pow(vr,1.10))));
          const x=bar.x;
          ctx.save();
          ctx.strokeStyle=c.color;ctx.fillStyle=c.color;ctx.lineWidth=Math.max(1,Math.min(1.6,spacing*.11));
          ctx.beginPath();ctx.moveTo(x,yh);ctx.lineTo(x,yl);ctx.stroke();
          const top=Math.min(yo,yc),bottom=Math.max(yo,yc),bodyH=Math.max(1,bottom-top);
          ctx.fillRect(x-bodyW/2,top,bodyW,bodyH);
          ctx.restore();
        }
      });
    }
  }

  class VolumeCandleSeries{
    constructor(){this._renderer=new VolumeCandleRenderer();}
    renderer(){return this._renderer;}
    update(data,_options){this._renderer.setData(data);}
    priceValueBuilder(d){return [d.high,d.low,d.close];}
    isWhitespace(d){return d.open===undefined||d.high===undefined||d.low===undefined||d.close===undefined;}
    defaultOptions(){return {color:'#089981'};}
    destroy(){}
  }

  function init(LWC){
    const candles=spec.candles||[];
    if(!candles.length){fail('Grafik verisi yok.');return;}
    loading.style.display='none';
    const mobile=window.matchMedia('(max-width:760px)').matches;
    const intraday=spec.period==='1h'||spec.period==='4h';
    const last=Number(spec.lastPrice)||0;
    const precision=precisionFor(last);

    const chart=LWC.createChart(container,{
      autoSize:true,
      layout:{background:{type:LWC.ColorType.Solid,color:'#ffffff'},textColor:'#4b5563',fontFamily:'-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',fontSize:mobile?12:13},
      grid:{vertLines:{visible:false},horzLines:{visible:true,color:'#eef1f4',style:LWC.LineStyle.Solid}},
      rightPriceScale:{visible:true,borderVisible:false,scaleMargins:{top:.08,bottom:.08},entireTextOnly:true},
      leftPriceScale:{visible:false},
      timeScale:{visible:true,borderVisible:false,timeVisible:intraday,secondsVisible:false,barSpacing:mobile?5.8:7.0,minBarSpacing:1.2,rightOffsetPixels:mobile?12:18,fixLeftEdge:false,fixRightEdge:false,lockVisibleTimeRangeOnResize:false},
      crosshair:{mode:LWC.CrosshairMode.Normal,vertLine:{color:'#aeb6c2',width:1,style:LWC.LineStyle.Dashed,labelVisible:false},horzLine:{color:'#aeb6c2',width:1,style:LWC.LineStyle.Dashed,labelVisible:true,labelBackgroundColor:'#4b5563'}},
      handleScroll:false,
      handleScale:false,
      kineticScroll:{mouse:false,touch:false},
      trackingMode:{exitMode:LWC.TrackingModeExitMode.OnTouchEnd},
      localization:{
        locale:'tr-TR',
        priceFormatter:p=>Number(p).toLocaleString('tr-TR',{minimumFractionDigits:precision,maximumFractionDigits:precision}),
        timeFormatter:t=>new Intl.DateTimeFormat('tr-TR',intraday?{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Istanbul'}:{day:'2-digit',month:'2-digit',year:'numeric',timeZone:'Europe/Istanbul'}).format(new Date(Number(t)*1000))
      }
    });

    const baseData=candles.map(c=>({time:c.time,open:c.open,high:c.high,low:c.low,close:c.close,vr:c.vr,color:c.color,volume:c.volume}));
    let baseSeries=null;
    try{
      baseSeries=chart.addCustomSeries(new VolumeCandleSeries(),{
        priceScaleId:'right',
        lastValueVisible:true,
        priceLineVisible:true,
        priceLineWidth:1,
        priceLineStyle:LWC.LineStyle.Dotted,
        priceFormat:{type:'price',precision:precision,minMove:Math.pow(10,-precision)}
      });
      baseSeries.setData(baseData);
    }catch(err){
      // Custom renderer desteklenmezse native candlestick'e düş; gesture davranışı yine TradingView motorunda kalır.
      console.warn('Custom volume candle fallback',err);
      baseSeries=chart.addSeries(LWC.CandlestickSeries,{
        upColor:'#089981',downColor:'#F23645',wickUpColor:'#089981',wickDownColor:'#F23645',borderVisible:false,
        priceScaleId:'right',lastValueVisible:true,priceLineVisible:true,priceLineStyle:LWC.LineStyle.Dotted,
        priceFormat:{type:'price',precision:precision,minMove:Math.pow(10,-precision)}
      });
      baseSeries.setData(baseData.map(c=>({time:c.time,open:c.open,high:c.high,low:c.low,close:c.close,color:c.color,borderColor:c.color,wickColor:c.color})));
    }

    const timeAtIndex=i=>candles[Math.max(0,Math.min(candles.length-1,Math.round(i)))]?.time;

    for(const ln of (spec.lines||[])){
      const pts=(ln.points||[]).map(p=>({time:timeAtIndex(p.i),value:Number(p.y)})).filter(p=>Number.isFinite(p.time)&&Number.isFinite(p.value));
      if(pts.length<2)continue;
      const s=chart.addSeries(LWC.LineSeries,{
        color:ln.color||'#2962FF',lineWidth:Math.max(1,Math.min(4,Math.round(Number(ln.width)||2))),
        lineStyle:ln.dashed?LWC.LineStyle.Dashed:LWC.LineStyle.Solid,
        crosshairMarkerVisible:false,lastValueVisible:false,priceLineVisible:false,priceScaleId:'right',
        autoscaleInfoProvider:()=>null
      });
      s.setData(pts);
    }

    // Alternasyon bölgesi: dolgu paneli yerine iki sade sınır çizgisi; mobil grafiği boğmaz.
    for(const rc of (spec.rects||[])){
      const t0=timeAtIndex(rc.x0),t1=timeAtIndex(rc.x1);
      for(const y of [Number(rc.y0),Number(rc.y1)]){
        if(!Number.isFinite(t0)||!Number.isFinite(t1)||!Number.isFinite(y))continue;
        const s=chart.addSeries(LWC.LineSeries,{color:rc.color||'#D5A800',lineWidth:1,lineStyle:LWC.LineStyle.Dashed,crosshairMarkerVisible:false,lastValueVisible:false,priceLineVisible:false,priceScaleId:'right',autoscaleInfoProvider:()=>null});
        s.setData([{time:t0,value:y},{time:t1,value:y}]);
      }
    }

    if(typeof LWC.createSeriesMarkers==='function' && (spec.markers||[]).length){
      const markers=(spec.markers||[]).map(m=>({
        time:timeAtIndex(m.i),
        position:'aboveBar',
        color:m.color||'#F59E0B',
        shape:m.shape==='cross'?'square':(m.shape==='star'?'circle':'arrowUp'),
        text:''
      })).filter(m=>Number.isFinite(m.time));
      try{LWC.createSeriesMarkers(baseSeries,markers,{autoScale:false});}catch(_e){}
    }

    // İlk görünüm: taramanın ilgili bölgesi. Sonrasında native TradingView pan/zoom tamamen serbest.
    const f=spec.focus||[Math.max(0,candles.length-80),candles.length+1];
    try{chart.timeScale().setVisibleLogicalRange({from:Number(f[0]),to:Number(f[1])});}catch(_e){chart.timeScale().fitContent();}

    // ------------------------------------------------------------------
    // V5.3 — TradingView benzeri deterministik gesture katmanı.
    // Lightweight Charts native gesture'ları iframe/iOS kombinasyonunda aynı
    // davranışı vermediği için public time/price range API'leri ile yönetilir.
    // Gövde: X+Y pan | sağ eksen: Y scale | alt eksen: X scale | pinch: X+Y zoom.
    // ------------------------------------------------------------------
    const root=document.getElementById(ROOT+'_root');
    root.style.touchAction='none';
    container.style.touchAction='none';
    root.style.userSelect='none';
    root.style.webkitUserSelect='none';
    root.style.webkitTouchCallout='none';

    const tScale=chart.timeScale();
    const pScale=chart.priceScale('right');
    let lastManualPriceRange=null;

    function clamp(v,a,b){return Math.max(a,Math.min(b,v));}
    function cloneRange(r){return r?{from:Number(r.from),to:Number(r.to)}:null;}
    function plotMetrics(){
      const rw=root.clientWidth||container.clientWidth||320;
      const rh=root.clientHeight||container.clientHeight||650;
      const pw=Math.max(46,Number(pScale.width?.()||58));
      const th=Math.max(24,Number(tScale.height?.()||28));
      return {rw,rh,pw,th,plotW:Math.max(80,rw-pw),plotH:Math.max(100,rh-th)};
    }
    function logicalRange(){
      const r=tScale.getVisibleLogicalRange?.();
      if(r&&Number.isFinite(r.from)&&Number.isFinite(r.to))return cloneRange(r);
      return {from:Math.max(0,candles.length-80),to:candles.length+1};
    }
    function inferredPriceRange(){
      const lr=logicalRange();
      const lo=Math.floor(lr.from)-2, hi=Math.ceil(lr.to)+2;
      let mn=Infinity,mx=-Infinity;
      for(let i=Math.max(0,lo);i<Math.min(candles.length,hi);i++){
        const c=candles[i]; if(!c)continue;
        mn=Math.min(mn,Number(c.low)); mx=Math.max(mx,Number(c.high));
      }
      if(!Number.isFinite(mn)||!Number.isFinite(mx)||mx<=mn){mn=last*.95;mx=last*1.05;}
      const pad=Math.max((mx-mn)*.08,Math.abs(mx)*.003,1e-6);
      return {from:mn-pad,to:mx+pad};
    }
    function priceRange(){
      const r=pScale.getVisibleRange?.();
      if(r&&Number.isFinite(r.from)&&Number.isFinite(r.to)&&r.to>r.from){lastManualPriceRange=cloneRange(r);return cloneRange(r);}
      return lastManualPriceRange?cloneRange(lastManualPriceRange):inferredPriceRange();
    }
    function setPriceRange(r){
      if(!r||!Number.isFinite(r.from)||!Number.isFinite(r.to)||r.to<=r.from)return;
      const mid=(r.from+r.to)/2;
      const minSpan=Math.max(Math.abs(mid)*1e-6,1e-7);
      if(r.to-r.from<minSpan){r={from:mid-minSpan/2,to:mid+minSpan/2};}
      pScale.setAutoScale(false);
      pScale.setVisibleRange(r);
      lastManualPriceRange=cloneRange(r);
    }
    function setLogicalRange(r){
      if(!r||!Number.isFinite(r.from)||!Number.isFinite(r.to)||r.to<=r.from)return;
      let span=clamp(r.to-r.from,6,Math.max(20,candles.length*3));
      const mid=(r.from+r.to)/2;
      tScale.setVisibleLogicalRange({from:mid-span/2,to:mid+span/2});
    }
    function zoneAt(x,y){
      const m=plotMetrics();
      if(x>=m.plotW)return 'price';
      if(y>=m.plotH)return 'time';
      return 'pane';
    }
    function pan2D(startLR,startPR,dx,dy){
      const m=plotMetrics();
      const lspan=startLR.to-startLR.from;
      const pspan=startPR.to-startPR.from;
      const shiftBars=-(dx/m.plotW)*lspan;
      // Parmak aşağı -> mumlar aşağı: görünür fiyat aralığını yukarı kaydır.
      const shiftPrice=(dy/m.plotH)*pspan;
      setLogicalRange({from:startLR.from+shiftBars,to:startLR.to+shiftBars});
      setPriceRange({from:startPR.from+shiftPrice,to:startPR.to+shiftPrice});
    }
    function scaleTime(startLR,dx,anchorX){
      const m=plotMetrics();
      const span=startLR.to-startLR.from;
      // Alt eksen sağa sürüklenirse daha fazla bar görünür; sola sürüklenirse yaklaşır.
      const factor=clamp(Math.exp(dx/220),.18,5.5);
      const frac=clamp(anchorX/m.plotW,0,1);
      const pivot=startLR.from+span*frac;
      const ns=clamp(span*factor,6,Math.max(20,candles.length*3));
      setLogicalRange({from:pivot-ns*frac,to:pivot+ns*(1-frac)});
    }
    function scalePrice(startPR,dy,anchorY){
      const m=plotMetrics();
      const span=startPR.to-startPR.from;
      // Sağ eksen yukarı sürükle -> zoom in; aşağı -> zoom out.
      const factor=clamp(Math.exp(dy/220),.16,6.0);
      const frac=clamp(anchorY/m.plotH,0,1); // top=0
      const pivot=startPR.to-span*frac;
      const ns=Math.max(Math.abs(pivot)*1e-7,span*factor);
      setPriceRange({from:pivot-ns*(1-frac),to:pivot+ns*frac});
    }
    function pinchBoth(startLR,startPR,startTouches,curTouches){
      const m=plotMetrics();
      const a0=startTouches[0],b0=startTouches[1],a=curTouches[0],b=curTouches[1];
      const sx0=Math.max(10,Math.abs(b0.x-a0.x)), sy0=Math.max(10,Math.abs(b0.y-a0.y));
      const sx=Math.max(10,Math.abs(b.x-a.x)), sy=Math.max(10,Math.abs(b.y-a.y));
      const d0=Math.max(20,Math.hypot(b0.x-a0.x,b0.y-a0.y));
      const d=Math.max(20,Math.hypot(b.x-a.x,b.y-a.y));
      const general=clamp(d0/d,.18,5.5);
      // Yatay/vertical ayrışma zayıfsa genel pinch oranını kullan.
      const fx=clamp((sx0>=24&&sx>=24)?sx0/sx:general,.18,5.5);
      const fy=clamp((sy0>=24&&sy>=24)?sy0/sy:general,.18,5.5);
      const cx=(a.x+b.x)/2, cy=(a.y+b.y)/2;
      const lspan=startLR.to-startLR.from, pspan=startPR.to-startPR.from;
      const xf=clamp(cx/m.plotW,0,1), yf=clamp(cy/m.plotH,0,1);
      const lpivot=startLR.from+lspan*xf;
      const ppivot=startPR.to-pspan*yf;
      const lnew=clamp(lspan*fx,6,Math.max(20,candles.length*3));
      const pnew=Math.max(Math.abs(ppivot)*1e-7,pspan*fy);
      setLogicalRange({from:lpivot-lnew*xf,to:lpivot+lnew*(1-xf)});
      setPriceRange({from:ppivot-pnew*(1-yf),to:ppivot+pnew*yf});
    }

    function localTouch(t){const r=root.getBoundingClientRect();return {id:t.identifier,x:t.clientX-r.left,y:t.clientY-r.top};}
    let gesture=null;
    root.addEventListener('touchstart',e=>{
      if(!e.touches?.length)return;
      const ts=Array.from(e.touches).map(localTouch);
      if(ts.length>=2){
        gesture={kind:'pinch',startTouches:ts.slice(0,2),startLR:logicalRange(),startPR:priceRange()};
      }else{
        const p=ts[0]; gesture={kind:zoneAt(p.x,p.y),x:p.x,y:p.y,startLR:logicalRange(),startPR:priceRange()};
      }
      e.preventDefault();
    },{passive:false,capture:true});
    root.addEventListener('touchmove',e=>{
      if(!gesture||!e.touches?.length)return;
      const ts=Array.from(e.touches).map(localTouch);
      if(gesture.kind==='pinch'&&ts.length>=2){pinchBoth(gesture.startLR,gesture.startPR,gesture.startTouches,ts.slice(0,2));}
      else if(ts.length===1){
        const p=ts[0],dx=p.x-gesture.x,dy=p.y-gesture.y;
        if(gesture.kind==='pane')pan2D(gesture.startLR,gesture.startPR,dx,dy);
        else if(gesture.kind==='price')scalePrice(gesture.startPR,dy,gesture.y);
        else if(gesture.kind==='time')scaleTime(gesture.startLR,dx,gesture.x);
      }
      e.preventDefault(); e.stopPropagation();
    },{passive:false,capture:true});
    root.addEventListener('touchend',e=>{if(!e.touches?.length)gesture=null;},{passive:false,capture:true});
    root.addEventListener('touchcancel',()=>{gesture=null;},{passive:false,capture:true});

    // PC: aynı üç bölge mouse ile de çalışsın.
    let mouseGesture=null;
    root.addEventListener('pointerdown',e=>{
      if(e.pointerType==='touch')return;
      const r=root.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
      mouseGesture={kind:zoneAt(x,y),x,y,startLR:logicalRange(),startPR:priceRange()};
      try{root.setPointerCapture(e.pointerId);}catch(_e){}
      e.preventDefault();
    },{capture:true});
    root.addEventListener('pointermove',e=>{
      if(!mouseGesture||e.pointerType==='touch')return;
      const r=root.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top,dx=x-mouseGesture.x,dy=y-mouseGesture.y;
      if(mouseGesture.kind==='pane')pan2D(mouseGesture.startLR,mouseGesture.startPR,dx,dy);
      else if(mouseGesture.kind==='price')scalePrice(mouseGesture.startPR,dy,mouseGesture.y);
      else if(mouseGesture.kind==='time')scaleTime(mouseGesture.startLR,dx,mouseGesture.x);
      e.preventDefault();
    },{capture:true});
    root.addEventListener('pointerup',()=>{mouseGesture=null;},{capture:true});
    root.addEventListener('pointercancel',()=>{mouseGesture=null;},{capture:true});
    root.addEventListener('wheel',e=>{
      const r=root.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
      if(zoneAt(x,y)==='price')scalePrice(priceRange(),e.deltaY*.35,y);
      else scaleTime(logicalRange(),e.deltaY*.35,x);
      e.preventDefault();
    },{passive:false,capture:true});

    // Çift tık/dokunma reset: eksen bölgesine göre.
    root.addEventListener('dblclick',e=>{
      const r=root.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top,z=zoneAt(x,y);
      if(z==='price'){pScale.setAutoScale(true);lastManualPriceRange=null;}
      else if(z==='time'){try{tScale.setVisibleLogicalRange({from:Number(f[0]),to:Number(f[1])});}catch(_e){tScale.fitContent();}}
      else {pScale.setAutoScale(true);lastManualPriceRange=null;try{tScale.setVisibleLogicalRange({from:Number(f[0]),to:Number(f[1])});}catch(_e){}}
      e.preventDefault();
    },{capture:true});

    // Tanılama: deploy sonrası hangi gesture motorunun çalıştığını net görebiliriz.
    window.__bistLwcDebug={chart,baseSeries,gestureEngine:'v5.3-public-range-controller',version:LWC.version?.()||'5.2.x',logicalRange,priceRange};
  }

  loadLibrary().then(init).catch(err=>fail('Grafik motoru yüklenemedi: '+(err?.message||err)));
})();
</script>
"""
    html = (html.replace("__UID__", uid)
                .replace("__HEIGHT__", str(int(height)))
                .replace("__MOBILE_HEIGHT__", str(int(mobile_height)))
                .replace("__PAYLOAD__", payload)
                .replace("__LWC_CDN__", LWC_CDN))
    components.html(html, height=mobile_height + 2, scrolling=False)


# Geriye dönük isim: uygulamanın başka bir yerinde çağrılırsa yeni motoru kullan.
def _render_canvas_chart(spec: Dict[str, Any], key: Optional[str] = None, height: int = 650) -> None:
    _render_lwc_chart(spec, key=key, height=height)


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
        _add_marker(spec, cross_idx, float(df["Close"].iloc[cross_idx]), "#D99B16", "star")
    tr = r.get("trendline") or {}
    if tr.get("matched") and tr.get("line"):
        ln = tr["line"]; s=_finite(ln.get("slope")); b=_finite(ln.get("intercept")); x1=int(ln.get("x1",0)); x2=int(ln.get("x2",0)); cx=int(tr.get("cross_idx",x2))
        if s is not None and b is not None:
            _add_line(spec,[{"i":x1,"y":s*x1+b},{"i":x2,"y":s*x2+b}],"#F23645",2.4,False)
            _add_line(spec,[{"i":x2,"y":s*x2+b},{"i":cx,"y":s*cx+b}],"#F23645",1.8,True)
            if 0<=cx<n:_add_marker(spec,cx,float(df["Close"].iloc[cx]),"#F23645","triangle")
    _render_lwc_chart(spec, key=key, height=650)


def render_triangle_chart(sym: str, r: Dict[str, Any], key: Optional[str] = None) -> None:
    df = r["df"].reset_index(drop=True); n=len(df)
    upper,lower=r["upper"],r["lower"]
    start=min(int(upper.get("x1",0)),int(lower.get("x1",0)))
    spec=_base_spec(df,str(r.get("period","")),max(0,start-8),n-1,"TRY")
    apex_x=_finite(r.get("apex_x"),n-1) or n-1; draw_to=min(float(apex_x), n-1+8)
    for ln,col in ((upper,"#F23645"),(lower,"#089981")):
        s=_finite(ln.get("slope"));b=_finite(ln.get("intercept"));x1=int(ln.get("x1",0));x2=int(ln.get("x2",0))
        if s is not None and b is not None:
            _add_line(spec,[{"i":x1,"y":s*x1+b},{"i":x2,"y":s*x2+b}],col,2.4,False)
            _add_line(spec,[{"i":x2,"y":s*x2+b},{"i":draw_to,"y":s*draw_to+b}],col,1.8,True)
    apy=_finite(r.get("apex_y"))
    if apy is not None:_add_marker(spec,int(round(apex_x)),apy,"#D99B16","cross")
    _render_lwc_chart(spec,key=key,height=650)


def render_trendline_chart(sym: str, r: Dict[str, Any], key: Optional[str] = None) -> None:
    df=r["df"].reset_index(drop=True); n=len(df); ln=r["line"]
    x1=int(ln.get("x1",0));x2=int(ln.get("x2",0));cross=int(r.get("cross_idx",x2))
    spec=_base_spec(df,str(r.get("period","")),max(0,x1-8),n-1,"TRY")
    s=_finite(ln.get("slope"));b=_finite(ln.get("intercept"))
    if s is not None and b is not None:
        _add_line(spec,[{"i":x1,"y":s*x1+b},{"i":x2,"y":s*x2+b}],"#F23645",2.5,False)
        _add_line(spec,[{"i":x2,"y":s*x2+b},{"i":cross,"y":s*cross+b}],"#F23645",1.8,True)
    if 0<=cross<n:_add_marker(spec,cross,float(df["Close"].iloc[cross]),"#F23645","triangle")
    _render_lwc_chart(spec,key=key,height=650)


def render_alternation_chart(sym: str, r: Dict[str, Any], key: Optional[str] = None) -> None:
    df=r["df"].reset_index(drop=True); n=len(df);start=int(r.get("start_idx",max(0,n-10)));end=int(r.get("end_idx",n-1))
    spec=_base_spec(df,str(r.get("period","")),max(0,start-20),n-1,"TRY")
    sub=df.iloc[max(0,start):min(n,end+1)]
    if not sub.empty:
        lo=float(sub["Low"].min());hi=float(sub["High"].max());pad=max((hi-lo)*.06,abs(hi)*.004,.01)
        spec["rects"].append({"x0":start-.5,"x1":end+.5,"y0":lo-pad,"y1":hi+pad,"color":"#D5A800","fill":"rgba(245,197,66,.04)","dashed":True})
        pts=[{"i":i,"y":float(df["Close"].iloc[i])} for i in range(max(0,start),min(n,end+1))]
        _add_line(spec,pts,"#D5A800",1.7,True)
    _render_lwc_chart(spec,key=key,height=650)
