# -*- coding: utf-8 -*-
"""
build_incentive_data.py — รวมตัวชี้วัดทุกตัวจาก "แหล่งข้อมูลกลาง" ให้เป็นไฟล์เดียว
สำหรับระบบแปลงตัวชี้วัดเป็นค่าเงิน (Rh1 Incentive)

หลักการเหล็ก: ทุกตัวเลขต้องมาจากฐานที่เขตถือเอง — ไม่ขอจาก รพ. / ไม่ขอจาก สสจ.
ผลลัพธ์: docs/data/incentive_data.json

แหล่งข้อมูล (ทั้งหมดอยู่บนเครื่อง/รันอัตโนมัติอยู่แล้ว)
  1.1 Planfin   : D:\\Github\\Planfin.V2\\comparison.html (RAW) ← GL master.parquet ผ่าน export_planfin.py
  1.2 ติด C/Deny: mishos.db  fact_breakdown eclaimdata_* (NUM/NUM_EDIT/NOEDIT) + statementuc NUM_VISIT
  1.3 TPS       : Rh1-TPS-V.3.1\\public\\data\\tps_scores.json (สำนักปลัด สธ. รายไตรมาส)
  1.4 Risk Score: dashboard.db records (คำนวณจาก D5317.mdb ทุกเดือนโดย run_all.py)
  ส่วนที่ 2 (K) : Rh1-BalanceSheet\\master.parquet (GL งบทดลองรายเดือน)

ใช้:  python build_incentive_data.py [t_now=256911] [t_prev=256811]
"""
import sys, os, json, re, sqlite3
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data")
os.makedirs(OUT, exist_ok=True)

DASH_DB   = r"D:\OneDrive\Share Rh1-New\0 Claude Cowork\Dashboard AI\Risk Score\dashboard.db"
MISHOS_DB = r"D:\OneDrive\Share Rh1-New\0 Claude Cowork\Routine AI\MISHOS\db\mishos.db"
PLANFIN   = r"D:\Github\Planfin.V2\comparison.html"
TPS_JSON  = r"D:\Github\Rh1-TPS-V.3.1\public\data\tps_scores.json"
PARQUET   = r"D:\Github\Rh1-BalanceSheet\master.parquet"
RISK_LIVE = r"D:\Github\Risk-Score-Rh1\index.html"   # RAW งวดสด (run_all.py เขียนทุกเดือน)

T_NOW  = int(sys.argv[1]) if len(sys.argv) > 1 else 256911   # งวด GL ล่าสุด (ส.ค. 69)
T_PREV = int(sys.argv[2]) if len(sys.argv) > 2 else 256811   # งวดเดียวกันปีก่อน
FY_NOW = T_NOW // 100

# บัญชีค่า K — ส่วนที่ 2 (รหัส 13 หลักหรือ prefix 10 หลัก)
K_ACC = {
    # หนี้ค่ารักษาตามจ่าย นอก CUP (เจ้าหนี้) — ลดลง = ดี
    "ap_outcup":  {"mode": "cr", "acc": ["2101020199.202"]},
    # รายได้กองทุนอื่น (ประกันสังคม / ต่างด้าว / กองทุนเฉพาะ) — เพิ่ม = ดี
    "fund_other": {"mode": "bs", "prefix": ["4301020106"]},
    # เงินสดจากช่องทางอื่น: ใบรับรองแพทย์ + ตรวจสุขภาพ
    "cash_other": {"mode": "bs", "acc": [
        "4313010199.105",   # รายได้ค่าใบรับรองแพทย์
        "4301020101.102",   # ตรวจสุขภาพ-หน่วยงานภาครัฐ
        "4301020102.102",   # ตรวจสุขภาพ-บุคคลภายนอก
        "4301020102.104",   # ตรวจสุขภาพ-หน่วยงานภาครัฐ
    ]},
}


def log(*a):
    print(*a, flush=True)


# ─────────────────────────── 1. ทะเบียนหน่วยบริการ (103 รพ.) ───────────────────────────
def load_hospitals():
    c = sqlite3.connect(DASH_DB)
    rows = c.execute("SELECT id, name, prov, type FROM hospitals").fetchall()
    c.close()
    out = {}
    for hid, name, prov, typ in rows:
        h = str(hid).zfill(5)
        out[h] = {"h": h, "name": name, "prov": prov, "type": typ}
    log(f"[hospitals] {len(out)} แห่ง")
    return out


# ─────────────────────────── 1.4 Risk Score รายเดือน ───────────────────────────
def load_risk(hosp):
    """อ่าน RAW จาก Risk-Score-Rh1/index.html (ไฟล์ที่ run_all.py อัปเดตทุกเดือน)
    — ใหม่กว่า dashboard.db เสมอ ห้ามสลับกลับไปใช้ dashboard.db โดยไม่เทียบงวดก่อน"""
    s_ = open(RISK_LIVE, encoding="utf-8", errors="replace").read()
    i = s_.index("RAW = {")
    b = s_[i + 6:]
    depth = 0
    for j, ch in enumerate(b):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                b = b[: j + 1]
                break
    recs = json.loads(b)["records"]
    per, fin = defaultdict(dict), defaultdict(dict)
    for r in recs:
        if int(r.get("fy") or 0) != FY_NOW or r.get("risk") is None:
            continue
        h = str(int(r["id"])).zfill(5)
        per[h][int(r["sort_key"])] = int(r["risk"])
        fin[h][int(r["sort_key"])] = {k: r.get(k) for k in ("CR", "QR", "Cash", "NWC", "NI", "EBITDA")}
    out = {}
    for h, mm in per.items():
        keys = sorted(mm)
        vals = [mm[k] for k in keys]
        out[h] = {
            "months": keys,
            "vals": vals,
            "max": max(vals),
            "last": vals[-1],
            "n": len(vals),
            "n_zero": sum(1 for v in vals if v == 0),
            "n_le5": sum(1 for v in vals if v <= 5),
        }
        last_t = keys[-1]
        out[h].update({("f_" + k): v for k, v in (fin[h].get(last_t) or {}).items()})
    any_h = next(iter(out))
    log(f"[risk] FY{FY_NOW} {len(out)} แห่ง / {len(out[any_h]['months'])} งวด (ถึง {out[any_h]['months'][-1]})")
    return out


# ─────────────────────────── 1.3 TPS (รายไตรมาส) ───────────────────────────
def load_tps():
    rows = json.load(open(TPS_JSON, encoding="utf-8"))
    latest = max(r["period_id"] for r in rows)
    out = {}
    for r in rows:
        if r["period_id"] != latest:
            continue
        h = str(r["hospital_code"]).zfill(5)
        try:
            sc = float(r["tps_score"])
        except (TypeError, ValueError):
            sc = None
        out[h] = {"score": sc, "grade": (r.get("grade") or "").strip(), "period": latest}
    log(f"[tps] งวด {latest} — {len(out)} แห่ง")
    return out, latest


# ─────────────────────────── 1.1 Planfin แผน vs ผลจริง ───────────────────────────
def norm_name(x):
    """ตัด 'รพ.' + ช่องว่าง เพื่อ match ชื่อข้ามแหล่ง (พิสูจน์แล้ว 103/103 ใน compute_planfin_actuals.py)"""
    return str(x).replace("รพ.", "").replace(" ", "").strip()


def load_planfin(hosp):
    raw = open(PLANFIN, encoding="utf-8", errors="replace").read()
    i = raw.index("var RAW=")
    body = raw[i + len("var RAW="):]
    depth = 0
    for j, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                body = body[: j + 1]
                break
    body = re.sub(r"(\d{4}):\s*\[", lambda m: '"' + m.group(1) + '":[', body)   # 2569:[ -> "2569":[
    data = json.loads(body)
    year = str(max(int(k) for k in data))
    by_name = {norm_name(v["name"]): h for h, v in hosp.items()}
    out, miss = {}, []
    for r in data[year]:
        h = str(r["id"]).zfill(5) if r.get("id") else by_name.get(norm_name(r.get("name", "")))
        if not h:
            miss.append(r.get("name"))
            continue
        out[h] = {
            "ip": r.get("ip"), "ia": r.get("ia"),
            "ep": r.get("ep"), "ea": r.get("ea"),
            "ipct": r.get("ipct"), "epct": r.get("epct"),
        }
    log(f"[planfin] ปีงบ {year} — {len(out)} แห่ง" + (f" ⚠ จับคู่ชื่อไม่ได้ {miss}" if miss else ""))
    return out, year


# ─────────────────────────── 1.2 ติด C / ค้างแก้ / Deny ───────────────────────────
def load_claim():
    c = sqlite3.connect(MISHOS_DB)
    rows = c.execute(
        """SELECT hcode, metric, SUM(value) FROM fact_breakdown
           WHERE report_key LIKE 'eclaimdata%' AND fy_be=?
           GROUP BY hcode, metric""", (FY_NOW,)
    ).fetchall()
    vis = c.execute(
        """SELECT hcode, SUM(value) FROM fact_breakdown
           WHERE report_key='statementuc' AND fy_be=? AND metric='NUM_VISIT'
           GROUP BY hcode""", (FY_NOW,)
    ).fetchall()
    amt = c.execute(
        """SELECT hcode, SUM(value) FROM fact_breakdown
           WHERE report_key='statementuc' AND fy_be=? AND metric='ACT_AMT'
           GROUP BY hcode""", (FY_NOW,)
    ).fetchall()
    c.close()

    agg = defaultdict(dict)
    for h, m, v in rows:
        agg[str(h).zfill(5)][m] = v or 0
    visits = {str(h).zfill(5): v or 0 for h, v in vis}
    amts = {str(h).zfill(5): v or 0 for h, v in amt}

    out = {}
    for h, m in agg.items():
        num = m.get("NUM", 0)
        noedit = m.get("NOEDIT", 0)
        v = visits.get(h, 0)
        out[h] = {
            "num": num,
            "edit": m.get("NUM_EDIT", 0),
            "noedit": noedit,
            "visits": v,
            "act_amt": amts.get(h, 0),
            # ติด C ต่อ 1,000 ครั้งบริการ (ต่ำ = ดี)
            "c_per_1k": round(num / v * 1000, 2) if v else None,
            # % ที่ยังไม่แก้ไข = เงินที่ยังไม่ได้จริง (ต่ำ = ดี)
            "unfix_pct": round(noedit / num * 100, 2) if num else None,
        }
    log(f"[claim] FY{FY_NOW} — {len(out)} แห่ง")
    return out


# ─────────────────────────── ส่วนที่ 2: ค่า K จาก GL ───────────────────────────
def load_k():
    import pandas as pd
    df = pd.read_parquet(PARQUET, columns=["t", "org5", "acc", "prefix", "bs", "EndDr", "EndCr"])
    df = df[df["t"].isin([T_NOW, T_PREV])]

    def pick(spec):
        if "acc" in spec:
            return df[df["acc"].isin(spec["acc"])]
        pre = [int(p) for p in spec["prefix"]]
        return df[df["prefix"].isin(pre)]

    out = defaultdict(dict)
    for key, spec in K_ACC.items():
        sub = pick(spec)
        if spec["mode"] == "cr":
            sub = sub.assign(val=sub["EndCr"] - sub["EndDr"])
        else:
            sub = sub.assign(val=sub["bs"])
        g = sub.groupby(["org5", "t"])["val"].sum().unstack(fill_value=0.0)
        for org, row in g.iterrows():
            now = float(row.get(T_NOW, 0.0))
            prev = float(row.get(T_PREV, 0.0))
            out[str(org).zfill(5)][key] = {
                "now": round(now, 2),
                "prev": round(prev, 2),
                "diff": round(now - prev, 2),
                "pct": round((now - prev) / prev * 100, 2) if prev else None,
            }
    log(f"[K] งวด {T_NOW} เทียบ {T_PREV} — {len(out)} แห่ง × {len(K_ACC)} ตัว")
    return out


# ─────────────────────────── main ───────────────────────────
def main():
    hosp = load_hospitals()
    risk = load_risk(hosp)
    tps, tps_period = load_tps()
    planfin, planfin_year = load_planfin(hosp)
    claim = load_claim()
    k = load_k()

    import datetime as _dt
    def _asof(path):
        try: return _dt.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
        except OSError: return None
    as_of = {
        "risk": _asof(RISK_LIVE), "k": _asof(PARQUET), "planfin": _asof(PLANFIN),
        "tps": _asof(TPS_JSON), "claim": _asof(MISHOS_DB),
    }
    log("[as_of] " + " · ".join(f"{a}={b}" for a, b in as_of.items()))

    only = set(hosp)
    payload = {
        "meta": {
            "generated": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
            "fy": FY_NOW,
            "t_now": T_NOW,
            "t_prev": T_PREV,
            "tps_period": tps_period,
            "planfin_year": planfin_year,
            "n_hosp": len(hosp),
            "as_of": as_of,
            "sources": {
                "planfin": "Planfin.V2/comparison.html ← GL master.parquet (export_planfin.py)",
                "claim": "mishos.db fact_breakdown (eclaimdata_*, statementuc) ← สปสช. MIS",
                "tps": f"Rh1-TPS-V.3.1/public/data/tps_scores.json (งวด {tps_period})",
                "risk": f"dashboard.db records ← D5317.mdb (run_all.py) FY{FY_NOW}",
                "k": f"Rh1-BalanceSheet/master.parquet งวด {T_NOW} เทียบ {T_PREV}",
            },
        },
        "hospitals": [hosp[h] for h in sorted(hosp)],
        "risk": {h: v for h, v in risk.items() if h in only},
        "tps": {h: v for h, v in tps.items() if h in only},
        "planfin": {h: v for h, v in planfin.items() if h in only},
        "claim": {h: v for h, v in claim.items() if h in only},
        "k": {h: v for h, v in k.items() if h in only},
    }

    # รายงานความครบถ้วน (ไม่เงียบ — ตัวไหนขาดต้องเห็น)
    cover = {kk: sum(1 for h in hosp if h in payload[kk]) for kk in ("risk", "tps", "planfin", "claim", "k")}
    payload["meta"]["coverage"] = cover
    log("[coverage] " + " · ".join(f"{a}={b}/{len(hosp)}" for a, b in cover.items()))

    path = os.path.join(OUT, "incentive_data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"[out] {path}  ({os.path.getsize(path)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
