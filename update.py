# -*- coding: utf-8 -*-
"""QLD 모으기 대시보드 자료
규칙: ① 매달 첫 거래일 30만원 QLD 무조건
      ② 지난달 등락률(지난달 종가 / 그 전달 종가 − 1)이 −13% 이하면 추가: −13~−20% 100만 · −20~−30% 200만 · −30% 이하 300만
표준 라이브러리만 쓴다(깃허브 액션에서 그대로 돈다). 출력 data.json
    python update.py
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 300_000
TIERS = [(-30.0, 3_000_000, "3단계"), (-20.0, 2_000_000, "2단계"), (-13.0, 1_000_000, "1단계")]
UA = {"User-Agent": "Mozilla/5.0"}


def tier(ret):
    for thr, amt, name in TIERS:
        if ret <= thr:
            return amt, name
    return 0, ""


def daily():
    """QLD 일봉 종가 전 기간. range=max는 바를 성기게 주므로 period1/period2로 받는다"""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/QLD?period1=1136073600&period2=%d&interval=1d" % int(time.time() + 86400))
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]
    ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
    meta = res["meta"]
    rows = []
    for t, c in zip(ts, cl):
        d = (datetime.fromtimestamp(t, tz=timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")   # 미국 동부 날짜
        rows.append([d, c])
    # 야후가 마지막 바 종가를 비워 줄 때가 있다 → meta 현재가로 채움(qld_signal과 같은 처리)
    if rows and rows[-1][1] is None and meta.get("regularMarketPrice"):
        rows[-1][1] = float(meta["regularMarketPrice"])
    rows = [(d, float(c)) for d, c in rows if c is not None]
    dedup = {}
    for d, c in rows:
        dedup[d] = c
    return sorted(dedup.items()), meta


def irr_year(paid, final):
    """매달 초 paid[k]를 넣고 마지막 달 말에 final이면 연 수익률(적금 이자처럼 돈이 들어간 시점마다 따로 굴린 값). 이분법"""
    n = len(paid)

    def gap(r):
        return sum(p * (1 + r) ** (n - k) for k, p in enumerate(paid)) - final
    lo, hi = -0.2, 0.2
    if gap(lo) > 0 or gap(hi) < 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if gap(mid) > 0:
            hi = mid
        else:
            lo = mid
    return ((1 + (lo + hi) / 2) ** 12 - 1) * 100


def pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def forward(months, done_months, mend, first_close, ret_of, years=(3, 5, 10)):   # 15년은 겹치지 않는 표본이 1개라 뺐다
    """앞으로 기대: 규칙을 «과거의 모든 시작 달»에서 N년 돌린 결과 분포(QLD 실제 가격, 가정 없음).
    시작 달마다 매달 첫 거래일 종가에 30만 + 지난달 등락 추가분을 사고, N년째 마지막 달 종가로 평가"""
    out = []
    for N in years:
        L = 12 * N
        rows = []
        for s in range(1, len(done_months) - L + 1):           # 상장 달(2006-06)은 반달이라 다음 달부터
            ms = done_months[s:s + L]
            sh = paid = 0.0
            paid_list, worst = [], 0.0
            for m in ms:
                yy, mm = map(int, m.split("-"))
                prev = "%04d-%02d" % (yy - (mm == 1), 12 if mm == 1 else mm - 1)
                amt = BASE + (tier(ret_of[prev])[0] if prev in ret_of else 0)
                sh += amt / first_close[m]
                paid += amt
                paid_list.append(amt)
                worst = min(worst, sh * mend[m][1] / paid - 1)
            val = sh * mend[ms[-1]][1]
            rows.append(dict(s=ms[0], e=ms[-1], paid=round(paid), val=round(val), x=round(val / paid, 3),
                             irr=irr_year(paid_list, val), worst=round(worst * 100, 1)))
        if not rows:
            continue
        xs = [r["x"] for r in rows]
        lo = min(rows, key=lambda r: r["x"])
        hi = max(rows, key=lambda r: r["x"])
        med_row = sorted(rows, key=lambda r: r["x"])[len(rows) // 2]
        irrs = [r["irr"] for r in rows if r["irr"] is not None]
        out.append(dict(
            n=N, cnt=len(rows), first=rows[0]["s"], last=rows[-1]["s"], base_paid=BASE * L,
            paid_med=round(pct([r["paid"] for r in rows], 0.5)), paid_max=max(r["paid"] for r in rows),
            x10=round(pct(xs, 0.1), 2), x50=round(pct(xs, 0.5), 2), x90=round(pct(xs, 0.9), 2),
            irr10=round(pct(irrs, 0.1), 1), irr50=round(pct(irrs, 0.5), 1), irr90=round(pct(irrs, 0.9), 1),
            below=round(100.0 * sum(r["x"] < 1 for r in rows) / len(rows), 1),
            worst_med=round(pct([r["worst"] for r in rows], 0.5), 1), worst_min=min(r["worst"] for r in rows),
            lo=lo, hi=hi, med=med_row,
            pts=[[r["s"], r["x"], r["paid"], r["val"]] for r in rows],
        ))
    return out


def main():
    rows, meta = daily()
    now_utc = datetime.now(timezone.utc)
    if os.environ.get("DASH_NOW"):                                   # 시험용: 시각을 바꿔 월말 전환을 확인
        now_utc = datetime.strptime(os.environ["DASH_NOW"], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        et = now_utc.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:                                                     # 윈도 파이썬에 tzdata가 없으면 대략값
        et = now_utc.replace(tzinfo=None) - timedelta(hours=4 if 3 <= now_utc.month <= 10 else 5)
    last_d, last_c = rows[-1]
    live = (last_d == et.strftime("%Y-%m-%d")) and (et.hour < 16) and et.weekday() < 5
    # 월말 종가·첫 거래일 종가
    mend, mfirst = {}, {}
    for d, c in rows:
        m = d[:7]
        mend[m] = (d, c)
        if m not in mfirst:
            mfirst[m] = (d, c)
    months = sorted(mend)
    # 마지막 달이 끝났나: 미국 날짜가 이미 다음 달이거나, 장 마감 뒤인데 다음 평일이 다음 달이면(휴장일은 무시)
    ld = datetime.strptime(last_d, "%Y-%m-%d")
    nxt_wd = ld + timedelta(days=1)
    while nxt_wd.weekday() >= 5:
        nxt_wd += timedelta(days=1)
    month_done = (et.strftime("%Y-%m") != last_d[:7]) or (not live and nxt_wd.strftime("%Y-%m") != last_d[:7])
    cur_m = None if month_done else last_d[:7]
    hist = []
    for i in range(1, len(months)):
        m, p = months[i], months[i - 1]
        ret = (mend[m][1] / mend[p][1] - 1) * 100
        done = not (m == cur_m)        # 이번 달은 진행 중(끝났으면 cur_m = None)
        amt, name = tier(ret)
        hist.append(dict(m=m, ret=round(ret, 2), close=round(mend[m][1], 2), done=done, extra=amt if done else 0, stage=name if done else ""))
    # 이번 달(진행 중)과 지난달
    cur = hist[-1] if not hist[-1]["done"] else None
    last_done = [h for h in hist if h["done"]][-1]
    mtd = cur["ret"] if cur else None
    # 다음에 살 달 = 마지막으로 끝난 달의 다음 달
    y, mo = map(int, last_done["m"].split("-"))
    nxt = "%04d-%02d" % (y + (mo == 12), 1 if mo == 12 else mo + 1)
    buy_extra, buy_stage = tier(last_done["ret"])
    # 신호 이력(추가 매수가 실제로 나간 달 = 신호 다음 달)
    signals = []
    for h in hist:
        if h["done"] and h["extra"] > 0:
            yy, mm = map(int, h["m"].split("-"))
            signals.append(dict(signal=h["m"], buy="%04d-%02d" % (yy + (mm == 12), 1 if mm == 12 else mm + 1), ret=h["ret"], extra=h["extra"], stage=h["stage"]))
    by_year = {}
    for s in signals:
        by_year[s["buy"][:4]] = by_year.get(s["buy"][:4], 0) + s["extra"]
    # 백테스트: 시작 연도마다 (규칙) vs (30만만) vs (규칙과 같은 돈을 매달 고르게)
    first_close = {m: mfirst[m][1] for m in months}
    ret_of = {h["m"]: h["ret"] for h in hist if h["done"]}
    bt = []
    for sy in range(2007, int(last_d[:4])):
        ms = [m for m in months if m >= "%d-01" % sy]
        flows = []
        for m in ms:
            yy, mm = map(int, m.split("-"))
            prev = "%04d-%02d" % (yy - (mm == 1), 12 if mm == 1 else mm - 1)
            ex = tier(ret_of[prev])[0] if prev in ret_of else 0          # 시작 첫 달도 지난달 등락으로 판정(규칙 그대로)
            flows.append((m, BASE, ex))
        px_end = last_c
        sh_a = sum((b + e) / first_close[m] for m, b, e in flows)
        sh_b = sum(b / first_close[m] for m, b, e in flows)
        paid_a = sum(b + e for m, b, e in flows)
        even = paid_a / len(flows)
        sh_c = sum(even / first_close[m] for m, b, e in flows)
        # 규칙 계좌의 최악 낙폭(월말 기준, 고점 대비)
        sh, pk, mdd, paid, worst, mdd_m = 0.0, 0.0, 0.0, 0.0, 0.0, ""
        for m, b, e in flows:
            sh += (b + e) / first_close[m]
            paid += b + e
            v = sh * mend[m][1]
            pk = max(pk, v)
            if v / pk - 1 < mdd:
                mdd, mdd_m = v / pk - 1, m
            worst = min(worst, v / paid - 1)
        bt.append(dict(y=sy, paid=round(paid_a), base_paid=BASE * len(flows), a=round(sh_a * px_end), b=round(sh_b * px_end), c=round(sh_c * px_end),
                       vs_base=round((sh_a / sh_b - 1) * 100, 1), vs_same=round((sh_a / sh_c - 1) * 100, 1), mdd=round(mdd * 100, 1), mdd_m=mdd_m, worst=round(worst * 100, 1)))
    done_months = [h["m"] for h in hist if h["done"]]
    fwd = forward(months, [months[0]] + done_months, mend, first_close, ret_of)
    m0, m1 = months[1], done_months[-1]                              # 상장 다음 달 첫 거래일 → 마지막 끝난 달 말
    yrs = (int(m1[:4]) * 12 + int(m1[5:]) - int(m0[:4]) * 12 - int(m0[5:]) + 1) / 12.0
    qld_cagr = ((mend[m1][1] / first_close[m0]) ** (1 / yrs) - 1) * 100
    out = dict(
        built=now_utc.strftime("%Y-%m-%d %H:%M UTC"), last_date=last_d, last_close=round(last_c, 2), live=live,
        fwd=fwd, qld_cagr=round(qld_cagr, 1), qld_span=[m0, m1],
        rule=dict(base=BASE, tiers=[dict(thr=t, amt=a, stage=n) for t, a, n in TIERS]),
        cur_month=cur_m if cur else None, mtd=mtd, prev_close=round(mend[months[-2]][1], 2) if cur else None, month_done=month_done,
        trig=[dict(stage=n, amt=a, px=round(mend[months[-2]][1] * (1 + t / 100), 2)) for t, a, n in TIERS] if cur else None,
        last_done=last_done, next_buy=dict(month=nxt, base=BASE, extra=buy_extra, stage=buy_stage, total=BASE + buy_extra),
        hist=hist, signals=signals, by_year=by_year, bt=bt,
    )
    with open(os.path.join(HERE, "data.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("마지막 %s %.2f%s · 끝난 달 %s %+.2f%% → %s 매수 %d원(추가 %d) · 진행 중 %s %s"
          % (last_d, last_c, " (장중)" if live else "", last_done["m"], last_done["ret"], nxt, BASE + buy_extra, buy_extra, cur_m if cur else "끝남",
             ("%+.2f%%" % mtd) if mtd is not None else "-"))
    print("신호 %d번 · 연도별 추가 %s" % (len(signals), by_year))
    print("QLD %s~%s 연 %.1f%%" % (m0, m1, qld_cagr))
    for f in fwd:
        print("  %d년 창 %d개(%s~%s 시작) · 넣은 돈 가운데 %.0f만(기본 %.0f만, 최대 %.0f만) · 배수 하위10%% %.2f / 가운데 %.2f / 상위10%% %.2f · 연 %.1f / %.1f / %.1f%% · 원금 밑 끝 %.1f%% · 중간 최악 가운데 %.0f%% 최악 %.0f%%"
              % (f["n"], f["cnt"], f["first"], f["last"], f["paid_med"] / 1e4, f["base_paid"] / 1e4, f["paid_max"] / 1e4, f["x10"], f["x50"], f["x90"],
                 f["irr10"], f["irr50"], f["irr90"], f["below"], f["worst_med"], f["worst_min"]))
        for k in ("lo", "med", "hi"):
            r = f[k]
            print("    %s %s~%s 넣은 %.0f만 → %.0f만 (%.2f배, 연 %s, 중간 최악 %.0f%%)" % (k, r["s"], r["e"], r["paid"] / 1e4, r["val"] / 1e4, r["x"],
                                                                         "-" if r["irr"] is None else "%.1f%%" % r["irr"], r["worst"]))
    for b in bt:
        print("  %d 시작: 넣은 돈 %.0f만(기본만 %.0f만) · 규칙 %.0f만 · 기본만 %.0f만 · 같은 돈 고르게 %.0f만 · 기본만 대비 %+.1f%% · 같은 돈 대비 %+.1f%% · 계좌 최악 낙폭 %.0f%% · 원금 대비 최악 %.0f%%"
              % (b["y"], b["paid"] / 1e4, b["base_paid"] / 1e4, b["a"] / 1e4, b["b"] / 1e4, b["c"] / 1e4, b["vs_base"], b["vs_same"], b["mdd"], b["worst"]))


if __name__ == "__main__":
    main()
