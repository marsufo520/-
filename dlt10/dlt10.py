#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大乐透 10期对奖助手（dlt10）—— 纯标准库，运行在 Linux box 上。

命令：
  add     新建追号计划（多注 × N 期，默认 10 期）
  list    查看所有计划进度
  run     抓取最新开奖 → 逐期对奖 → 写 results/report.html → 打印微信摘要
  import  从 JSON / CSV 导入计划
  report  只用本地缓存重新生成报告与摘要（不联网）
  draws   查看本地缓存的最近开奖
  remove  删除计划

奖级规则：2026-01 起（第 26014 期起）新规则，7 个奖级；追加只参与一、二等奖（+80%）。
"""
import argparse, csv, datetime as dt, html, json, math, os, re, sys, time
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PLANS_FILE = os.path.join(HERE, "plans.json")
DRAWS_FILE = os.path.join(HERE, "draws.json")
RESULTS_FILE = os.path.join(HERE, "results.json")
REPORT_FILE = os.path.join(HERE, "report.html")
SUMMARY_FILE = os.path.join(HERE, "summary.txt")

NEW_RULE_FIRST_ISSUE = 26014          # 新规则首期
POOL_THRESHOLD = 800_000_000          # 奖池 8 亿门槛
FIXED_LOW = {3: 5000, 4: 300, 5: 150, 6: 15, 7: 5}
FIXED_HIGH = {3: 6666, 4: 380, 5: 200, 6: 18, 7: 7}
LEVEL_CN = {1: "一等奖", 2: "二等奖", 3: "三等奖", 4: "四等奖", 5: "五等奖", 6: "六等奖", 7: "七等奖"}
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
BASE_PRICE, ADDON_PRICE = 2, 1
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
WEEK = "一二三四五六日"


# ───────────────────────── 基础工具 ─────────────────────────
def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def now_str():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def to_int(s):
    if s is None:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    s = re.sub(r"[^\d.\-]", "", str(s))
    if s in ("", "-", "."):
        return 0
    return int(float(s))


def yuan(n):
    return f"{n:,}"


def weekday_cn(date_s):
    try:
        return "周" + WEEK[dt.date.fromisoformat(date_s).weekday()]
    except Exception:
        return ""


# ───────────────────────── 号码解析 / 校验 ─────────────────────────
def parse_zhu(text):
    """'01 05 10 22 33 + 03 09' / '1,5,10,22,33|3,9' / '01 05 10 22 33 03 09' → (front, back)"""
    if isinstance(text, dict):
        front, back = text.get("front"), text.get("back")
    elif isinstance(text, (list, tuple)) and len(text) == 2 and isinstance(text[0], (list, tuple)):
        front, back = text
    else:
        s = str(text)
        parts = re.split(r"[+|/]|后区|：|:", s)
        parts = [p for p in parts if re.search(r"\d", p)]
        if len(parts) >= 2:
            front = re.findall(r"\d+", parts[0]); back = re.findall(r"\d+", " ".join(parts[1:]))
        else:
            nums = re.findall(r"\d+", s)
            front, back = nums[:5], nums[5:]
    front = sorted(int(x) for x in front); back = sorted(int(x) for x in back)
    validate_zhu(front, back, text)
    return front, back


def validate_zhu(front, back, raw=""):
    if len(front) != 5 or len(set(front)) != 5 or not all(1 <= x <= 35 for x in front):
        raise ValueError(f"前区必须是 5 个不重复的 01-35 号码：{raw}")
    if len(back) != 2 or len(set(back)) != 2 or not all(1 <= x <= 12 for x in back):
        raise ValueError(f"后区必须是 2 个不重复的 01-12 号码：{raw}")


def fmt_nums(nums):
    return " ".join(f"{n:02d}" for n in nums)


# ───────────────────────── 奖级规则 ─────────────────────────
def prize_level(fh, bh):
    """新规则（第26014期起）命中 → 奖级；0 表示未中奖。"""
    table = {
        (5, 2): 1, (5, 1): 2,
        (5, 0): 3, (4, 2): 3,
        (4, 1): 4,
        (4, 0): 5, (3, 2): 5,
        (3, 1): 6, (2, 2): 6,
        (3, 0): 7, (2, 1): 7, (1, 2): 7, (0, 2): 7,
    }
    return table.get((fh, bh), 0)


def pool_before(draw, draws_by_issue):
    """本期开奖时的奖池 = 上一期开奖后的奖池滚存。"""
    issues = sorted(i for i in draws_by_issue if int(i) < int(draw["issue"]))
    if not issues:
        return None
    prev = draws_by_issue[issues[-1]]
    p = prev.get("pool")
    return p if p else None


def level_amounts(draw, draws_by_issue):
    """返回 {level: {'base': 元或None, 'addon': 元或None, 'est': bool}}。
    优先使用官方公布的单注奖金；固定奖缺失时按上期奖池推断档位；浮动奖未公布则为 None（待公布）。"""
    out = {}
    lv = draw.get("levels") or {}
    pb = pool_before(draw, draws_by_issue)
    tier = None if pb is None else (FIXED_HIGH if pb >= POOL_THRESHOLD else FIXED_LOW)
    for L in range(1, 8):
        rec = lv.get(str(L)) or {}
        base = rec.get("amount") or 0
        addon = rec.get("addon_amount") or 0
        est = False
        if L >= 3:
            if not base:
                base = (tier or FIXED_LOW)[L]
                est = True
            addon = 0  # 追加仅参与浮动奖
        else:
            if not base:
                base = None
            if base and not addon:
                addon = int(round(base * 0.8)); est = True  # 规则第十六条：追加 = 基本 × 80%
            if not base:
                addon = None
        out[L] = {"base": base, "addon": addon, "est": est}
    out["_tier_note"] = ("8亿及以上档" if tier is FIXED_HIGH else "8亿以下档" if tier is FIXED_LOW else "档位未知")
    out["_pool_before"] = pb
    return out


def check_zhu(front, back, draw, amounts, addon):
    fh = sorted(set(front) & set(draw["front"])); bh = sorted(set(back) & set(draw["back"]))
    L = prize_level(len(fh), len(bh))
    res = {"front_hit": fh, "back_hit": bh, "hit": f"{len(fh)}+{len(bh)}", "level": L,
           "level_cn": LEVEL_CN.get(L, "未中奖"), "base": 0, "addon": 0, "prize": 0,
           "pending": False, "est": False}
    if L:
        a = amounts[L]
        if a["base"] is None:
            res["pending"] = True
        else:
            res["base"] = a["base"]
            res["addon"] = (a["addon"] or 0) if (addon and L <= 2) else 0
            res["prize"] = res["base"] + res["addon"]
            res["est"] = a["est"]
    return res


# ───────────────────────── 抓取开奖 ─────────────────────────
def http_get(url, headers=None, timeout=15, encoding=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "")
    if encoding is None:
        m = re.search(r"charset=([\w-]+)", ctype)
        encoding = m.group(1) if m else "utf-8"
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return raw.decode("gbk", errors="ignore")


def level_from_name(name):
    m = re.search(r"([一二三四五六七八九])等奖", name)
    return CN_NUM.get(m.group(1)) if m else None


def fetch_official(page_size=30, page_no=1):
    """体彩官方接口（webapi.sporttery.cn）。境外 IP 可能被 EdgeOne 拦截（HTTP 567）。"""
    headers = {"User-Agent": UA, "Referer": "https://static.sporttery.cn/",
               "Origin": "https://static.sporttery.cn",
               "Accept": "application/json, text/javascript, */*; q=0.01",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    data, err = None, None
    for ext in ("json", "qry"):
        url = (f"https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.{ext}"
               f"?gameNo=85&provinceId=0&pageSize={page_size}&isVerify=1&pageNo={page_no}")
        try:
            data = json.loads(http_get(url, headers)); break
        except Exception as e:
            err = e
    if data is None:
        raise err
    return parse_official(data)


def parse_official(data):
    if not data.get("success", True) or "value" not in data:
        raise RuntimeError(f"官方接口返回异常：{str(data)[:200]}")
    out = []
    for it in data["value"].get("list", []):
        nums = [int(x) for x in re.findall(r"\d+", it.get("lotteryDrawResult", ""))]
        if len(nums) < 7:
            continue
        levels = {}
        for p in it.get("prizeLevelList") or []:
            name = p.get("prizeLevel", ""); L = level_from_name(name)
            if not L:
                continue
            rec = levels.setdefault(str(L), {})
            amt = to_int(p.get("stakeAmount") or p.get("stakeAmountFormat"))
            cnt = to_int(p.get("stakeCount"))
            if "追加" in name:
                rec["addon_amount"], rec["addon_count"] = amt, cnt
            else:
                rec["amount"], rec["count"] = amt, cnt
        out.append({
            "issue": str(it["lotteryDrawNum"]),
            "date": (it.get("lotteryDrawTime") or "")[:10],
            "front": sorted(nums[:5]), "back": sorted(nums[5:7]),
            "pool": to_int(it.get("poolBalanceAfterdraw")),
            "sales": to_int(it.get("totalSaleAmount")),
            "levels": levels, "source": "体彩官方 webapi.sporttery.cn",
        })
    if not out:
        raise RuntimeError("官方接口无数据")
    return out


def fetch_500_history(limit=30, start=None, end=None):
    """500彩票网 datachart 历史表：号码、奖池、一二等奖、销售额。"""
    if start and end:
        url = f"https://datachart.500.com/dlt/history/newinc/history.php?start={start}&end={end}"
    else:
        url = f"https://datachart.500.com/dlt/history/newinc/history.php?limit={limit}&sort=0"
    t = http_get(url, {"User-Agent": UA, "Referer": "https://datachart.500.com/dlt/"})
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    out = []
    for row in re.findall(r"<tr class=\"t_tr1\">(.*?)</tr>", t, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 15 or not re.fullmatch(r"\d{5}", cells[0]):
            continue
        front = sorted(int(x) for x in cells[1:6]); back = sorted(int(x) for x in cells[6:8])
        levels = {}
        if to_int(cells[10]):
            levels["1"] = {"amount": to_int(cells[10]), "count": to_int(cells[9])}
        if to_int(cells[12]):
            levels["2"] = {"amount": to_int(cells[12]), "count": to_int(cells[11])}
        out.append({"issue": cells[0], "date": cells[14], "front": front, "back": back,
                    "pool": to_int(cells[8]), "sales": to_int(cells[13]),
                    "levels": levels, "source": "500彩票网 datachart"})
    if not out:
        raise RuntimeError("500 历史表解析失败")
    return out


def fetch_500_detail(issue):
    """500彩票网 单期详情：全部奖级单注奖金（含一、二等奖追加）。"""
    url = f"https://m.500.com/info/kaijiang/dlt/{issue}.shtml"
    t = http_get(url, {"User-Agent": UA_M}, encoding="gbk")
    balls = re.findall(r'class="(?:orange|blue)-ball ball">(\d+)<', t)
    if len(balls) < 7:
        raise RuntimeError(f"500 详情页解析失败：{issue}")
    m = re.search(r'c111 fs-12 mr5">(\d{5})期', t)
    if m and m.group(1) != str(issue):
        raise RuntimeError(f"500 详情页期号不符：{m.group(1)} != {issue}")
    md = re.search(r"(\d{4}-\d\d-\d\d)", t)
    date = md.group(1) if md else ""
    levels = {}
    for row in re.findall(r'<tr class="tr-line[^"]*">(.*?)</tr>', t, re.S):
        tok = [x.strip() for x in re.sub(r"<[^>]+>", "|", row).split("|") if x.strip()]
        if len(tok) < 3:
            continue
        L = level_from_name(tok[0])
        if not L:
            continue
        cnt, amt = to_int(tok[-2]), to_int(tok[-1])
        rec = levels.setdefault(str(L), {})
        if "追加" in tok[0]:
            if amt: rec["addon_amount"], rec["addon_count"] = amt, cnt
        elif amt:
            rec["amount"], rec["count"] = amt, cnt
    nums = [int(x) for x in balls[:7]]
    return {"issue": str(issue), "date": date, "front": sorted(nums[:5]), "back": sorted(nums[5:7]),
            "levels": levels, "source": "500彩票网 详情页"}


def fetch_gstc_latest():
    """甘肃体彩网（省级体彩中心官网）首页最新一期号码，仅作兜底/交叉核对。"""
    t = http_get("https://www.gstc.org.cn/wanfa/dlt", {"User-Agent": UA})
    m = re.search(r'sct-inline[^>]*>(\d{5})</a>.*?开奖日期：(\d{4}-\d\d-\d\d).*?开奖号码：(.*?)奖池', t, re.S)
    if not m:
        raise RuntimeError("甘肃体彩网解析失败")
    nums = [int(x) for x in re.findall(r">\s*(\d{2})\s*<", m.group(3))]
    if len(nums) < 7:
        raise RuntimeError("甘肃体彩网号码不足 7 个")
    return [{"issue": m.group(1), "date": m.group(2), "front": sorted(nums[:5]), "back": sorted(nums[5:7]),
             "levels": {}, "source": "甘肃体彩网"}]


def merge_draw(cache, d):
    old = cache.get(d["issue"])
    if old and (old["front"] != d["front"] or old["back"] != d["back"]):
        print(f"⚠️ 第{d['issue']}期号码不一致：缓存[{old.get('source')}] {fmt_nums(old['front'])}+{fmt_nums(old['back'])}"
              f" vs [{d['source']}] {fmt_nums(d['front'])}+{fmt_nums(d['back'])}，以官方/先到数据为准", file=sys.stderr)
        if "官方" not in d["source"]:
            return
        old = None
    new = dict(old or {})
    for k in ("issue", "date", "front", "back"):
        if d.get(k):
            new[k] = d[k]
    if d.get("pool"):
        new["pool"] = d["pool"]
    if d.get("sales"):
        new["sales"] = d["sales"]
    lv = new.setdefault("levels", {})
    for L, rec in (d.get("levels") or {}).items():
        cur = lv.setdefault(L, {})
        for k, v in rec.items():
            if v:
                cur[k] = v
    srcs = set(filter(None, (new.get("sources") or []) + [d["source"]]))
    new["sources"] = sorted(srcs); new["source"] = d["source"] if "官方" in d["source"] else new.get("source", d["source"])
    new["updated"] = now_str()
    cache[d["issue"]] = new


def needs_detail(d):
    lv = d.get("levels") or {}
    return not all((lv.get(str(L)) or {}).get("amount") for L in range(1, 8))


def update_draws(plans, verbose=True):
    cache = load_json(DRAWS_FILE, {})
    log = []
    got = None
    for name, fn in (("体彩官方接口", lambda: fetch_official(30)),
                     ("500彩票网历史表", lambda: fetch_500_history(30)),
                     ("甘肃体彩网", fetch_gstc_latest)):
        try:
            got = fn(); log.append(f"✅ {name}：{len(got)} 期，最新 {max(d['issue'] for d in got)}")
            for d in got:
                merge_draw(cache, d)
            break
        except Exception as e:
            log.append(f"❌ {name}：{type(e).__name__}: {str(e)[:120]}")
    # 非官方来源时，用省体彩中心官网交叉核对最新一期号码（省站通常晚几小时更新）
    if got and "官方" not in got[0]["source"]:
        try:
            g = fetch_gstc_latest()[0]; c = cache.get(g["issue"])
            if c:
                same = c["front"] == g["front"] and c["back"] == g["back"]
                log.append(f"{'✅' if same else '⚠️'} 交叉核对 甘肃体彩网 第{g['issue']}期：{'一致' if same else '不一致！'}")
        except Exception as e:
            log.append(f"· 交叉核对跳过：{str(e)[:80]}")
    # 计划起始期早于缓存 → 按期号区间补抓
    if plans:
        earliest = min(int(p["start_issue"]) for p in plans)
        if cache and earliest < min(int(i) for i in cache):
            try:
                more = fetch_500_history(start=earliest, end=max(int(i) for i in cache))
                for d in more:
                    merge_draw(cache, d)
                log.append(f"✅ 补抓历史 {earliest} 起 {len(more)} 期")
            except Exception as e:
                log.append(f"❌ 补抓历史失败：{e}")
    # 计划涉及的期 + 最新一期：补全各奖级单注奖金（官方数据已完整则跳过）
    want = set()
    if cache:
        want.add(max(cache, key=int))
    for p in plans:
        want.update(covered_issues(p, cache))
    for iss in sorted(want):
        d = cache.get(iss)
        if d and needs_detail(d) and int(iss) >= NEW_RULE_FIRST_ISSUE:
            try:
                merge_draw(cache, fetch_500_detail(iss)); time.sleep(0.3)
            except Exception as e:
                log.append(f"❌ 第{iss}期奖级详情：{e}")
    save_json(DRAWS_FILE, cache)
    if verbose:
        print("\n".join(log))
    return cache, log


# ───────────────────────── 计划 / 对奖 ─────────────────────────
def covered_issues(plan, cache):
    """计划覆盖的期：从起始期起，按期号顺序数到 N 期（只含已开奖的）。"""
    start = int(plan["start_issue"])
    return [i for i in sorted(cache, key=int) if int(i) >= start][: int(plan.get("periods", 10))]


def plan_cost_per_period(plan):
    return len(plan["tickets"]) * (BASE_PRICE + (ADDON_PRICE if plan.get("addon") else 0))


def evaluate(plans, cache):
    results = []
    for p in plans:
        per = plan_cost_per_period(p)
        rows = []
        for iss in covered_issues(p, cache):
            d = cache[iss]
            if int(iss) < NEW_RULE_FIRST_ISSUE:
                rows.append({"issue": iss, "date": d.get("date"), "draw_front": d["front"], "draw_back": d["back"],
                             "unsupported": True, "tickets": [], "cost": per, "prize": 0, "pending": False})
                continue
            amts = level_amounts(d, cache)
            tks = [dict(check_zhu(t["front"], t["back"], d, amts, p.get("addon")), front=t["front"], back=t["back"])
                   for t in p["tickets"]]
            rows.append({"issue": iss, "date": d.get("date"), "draw_front": d["front"], "draw_back": d["back"],
                         "tier": amts["_tier_note"], "tickets": tks, "cost": per,
                         "prize": sum(t["prize"] for t in tks), "pending": any(t["pending"] for t in tks),
                         "est": any(t["est"] and t["prize"] for t in tks)})
        done = len(rows)
        spent = per * done; won = sum(r["prize"] for r in rows)
        results.append({"id": p["id"], "name": p["name"], "addon": bool(p.get("addon")), "start_issue": p["start_issue"],
                        "periods": int(p.get("periods", 10)), "n_tickets": len(p["tickets"]),
                        "cost_per_period": per, "total_cost": per * int(p.get("periods", 10)),
                        "done": done, "spent": spent, "won": won, "net": won - spent, "rows": rows,
                        "pending": any(r["pending"] for r in rows)})
    return results


def load_plans():
    return load_json(PLANS_FILE, [])


def new_plan_id(plans):
    return max([p.get("id", 0) for p in plans] + [0]) + 1


def make_plan(plans, name, zhus, start, periods=10, addon=False, note=""):
    if not re.fullmatch(r"\d{5}", str(start)):
        raise ValueError(f"起始期号应为 5 位数字（如 26119）：{start}")
    if not zhus:
        raise ValueError("至少需要 1 注号码")
    tickets = []
    for z in zhus:
        f, b = parse_zhu(z)
        tickets.append({"front": f, "back": b})
    if any(p["name"] == name for p in plans):
        raise ValueError(f"已存在同名计划：{name}")
    return {"id": new_plan_id(plans), "name": name, "start_issue": str(start), "periods": int(periods),
            "addon": bool(addon), "tickets": tickets, "note": note, "created": now_str()}


def parse_bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y", "是", "追加", "on")


# ───────────────────────── 输出：文字摘要 ─────────────────────────
def latest_draw(cache):
    if not cache:
        return None
    return cache[max(cache, key=int)]


def text_summary(results, cache, fetch_ok=True):
    L = []
    d = latest_draw(cache)
    winners = [(r, row, t) for r in results for row in r["rows"] for t in row["tickets"] if t["level"]]
    big = [(r, row, t) for (r, row, t) in winners if t["level"] <= 6]
    latest_iss = d["issue"] if d else None
    latest_win = [(r, row, t) for (r, row, t) in winners if row["issue"] == latest_iss]
    if latest_win:
        tot = sum(t["prize"] for _, _, t in latest_win)
        L.append(f"🎉【本期中奖】第{latest_iss}期 共 {len(latest_win)} 注中奖，奖金 {yuan(tot)} 元"
                 + ("（含待公布浮动奖）" if any(t['pending'] for _, _, t in latest_win) else ""))
        for r, row, t in latest_win:
            amt = "金额待公布" if t["pending"] else f"{yuan(t['prize'])}元" + ("(追加)" if t["addon"] else "")
            L.append(f"  · {r['name']}：{fmt_nums(t['front'])}+{fmt_nums(t['back'])} 中{t['hit']} {t['level_cn']} {amt}")
        L.append("")
    L.append("【大乐透对奖】" + now_str() + "（北京时间）")
    if not fetch_ok:
        L.append("⚠️ 本次抓取开奖失败，以下为本地缓存数据，可稍后再执行 run。")
    if d:
        pool = f"，奖池 {d['pool']/1e8:.2f} 亿" if d.get("pool") else ""
        L.append(f"最新开奖：第{d['issue']}期 {d.get('date','')}{weekday_cn(d.get('date',''))}")
        L.append(f"前区 {fmt_nums(d['front'])} ｜ 后区 {fmt_nums(d['back'])}{pool}")
    else:
        L.append("暂无开奖数据。")
    if not results:
        L.append("")
        L.append("还没有录入号码，无法对奖。用 `python3 dlt10.py add` 或 `import` 录入追号计划。")
        return "\n".join(L)
    L.append("")
    for r in results:
        L.append(f"▶ {r['name']}（{r['n_tickets']}注/期{'·追加' if r['addon'] else ''}，起始{r['start_issue']}）进度 {r['done']}/{r['periods']} 期")
        last = r["rows"][-1] if r["rows"] else None
        if last:
            hits = "、".join(t["hit"] for t in last["tickets"])
            wins = [t for t in last["tickets"] if t["level"]]
            ws = "；".join(f"{t['level_cn']}" + ("(待公布)" if t["pending"] else f"{yuan(t['prize'])}元") for t in wins) or "未中奖"
            L.append(f"  第{last['issue']}期 命中 {hits} → {ws}")
        else:
            L.append(f"  尚未开奖（等待第{r['start_issue']}期）")
        L.append(f"  已投入 {yuan(r['spent'])} 元 ｜ 中奖 {yuan(r['won'])} 元 ｜ 净额 {r['net']:+,} 元"
                 + ("（有浮动奖待公布）" if r["pending"] else "")
                 + ("（含按规则推算的追加奖金）" if any(t["est"] and t["addon"] for row in r["rows"] for t in row["tickets"]) else ""))
    ts = sum(r["spent"] for r in results); tw = sum(r["won"] for r in results)
    L.append("")
    L.append(f"合计：投入 {yuan(ts)} 元，中奖 {yuan(tw)} 元，净额 {tw-ts:+,} 元")
    if not latest_win and any(row["issue"] == latest_iss for r in results for row in r["rows"]):
        L.append("本期未中奖。")
    L.append(f"报告：{REPORT_FILE}")
    return "\n".join(L)


# ───────────────────────── 输出：HTML 报告 ─────────────────────────
CSS = """
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f4f5f7;color:#222;font-size:15px}
header{background:linear-gradient(135deg,#c81e1e,#e8562a);color:#fff;padding:16px 14px}
header h1{margin:0 0 4px;font-size:20px}header .sub{opacity:.9;font-size:13px}
.wrap{max-width:760px;margin:0 auto;padding:10px}
.card{background:#fff;border-radius:12px;padding:12px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.ball{display:inline-block;width:28px;height:28px;line-height:28px;text-align:center;border-radius:50%;margin:2px;font-weight:600;font-size:13px}
.f{background:#fde8e8;color:#c81e1e;border:1px solid #f3b4b4}.b{background:#e6efff;color:#1e4fc8;border:1px solid #b4c8f3}
.f.hit{background:#e02424;color:#fff;border-color:#e02424}.b.hit{background:#1c64f2;color:#fff;border-color:#1c64f2}
.df{background:#e02424;color:#fff}.db{background:#1c64f2;color:#fff}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:8px 0}
.stat{background:#f8f9fb;border-radius:8px;padding:8px;text-align:center}.stat .v{font-size:17px;font-weight:700}.stat .k{font-size:12px;color:#777}
.pos{color:#0e9f6e}.neg{color:#e02424}
.bar{height:8px;background:#eee;border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:#e8562a}
details{border-top:1px solid #eee;padding:6px 0}summary{cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap}
summary::-webkit-details-marker{display:none}
.zhu{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:4px 0;border-bottom:1px dashed #f0f0f0}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;background:#eee;color:#555}
.tag.win{background:#fff3c4;color:#9a5b00;font-weight:700}.tag.big{background:#e02424;color:#fff}
.muted{color:#888;font-size:12px}.banner{background:#fff3c4;border:1px solid #f5d76e;color:#7a4b00;padding:10px;border-radius:10px;font-weight:600}
table.lv{width:100%;border-collapse:collapse;font-size:13px}table.lv td,table.lv th{border-bottom:1px solid #eee;padding:4px;text-align:center}
footer{color:#999;font-size:12px;text-align:center;padding:16px}
"""


def balls(nums, cls, hits=()):
    return "".join(f'<span class="ball {cls}{" hit" if n in hits else ""}">{n:02d}</span>' for n in nums)


def render_html(results, cache, fetch_log=None):
    e = html.escape
    d = latest_draw(cache)
    parts = [f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>大乐透 10期对奖助手</title><style>{CSS}</style></head><body>"
             f"<header><h1>🎯 大乐透 10期对奖助手</h1><div class='sub'>更新于 {now_str()}（北京时间）</div></header><div class='wrap'>"]
    wins = [(r, row, t) for r in results for row in r["rows"] for t in row["tickets"] if t["level"]]
    if d and any(row["issue"] == d["issue"] for r, row, t in wins):
        parts.append(f"<div class='banner'>🎉 第{d['issue']}期有中奖！详见下方计划。</div>")
    if d:
        pool = f"奖池 {d['pool']/1e8:.2f} 亿元" if d.get("pool") else "奖池待公布"
        parts.append(f"<div class='card'><b>最新开奖 · 第{d['issue']}期</b> <span class='muted'>{e(d.get('date',''))} {weekday_cn(d.get('date',''))} · {pool}</span><div style='margin-top:6px'>"
                     f"{balls(d['front'],'df')} {balls(d['back'],'db')}</div>")
        amts = level_amounts(d, cache)
        parts.append("<table class='lv' style='margin-top:8px'><tr><th>奖级</th><th>条件</th><th>单注奖金</th><th>追加</th></tr>")
        conds = {1: "5+2", 2: "5+1", 3: "5+0 / 4+2", 4: "4+1", 5: "4+0 / 3+2", 6: "3+1 / 2+2", 7: "3+0 / 2+1 / 1+2 / 0+2"}
        for L in range(1, 8):
            a = amts[L]
            base = "待公布" if a["base"] is None else yuan(a["base"]) + ("*" if a["est"] and L >= 3 else "")
            add = "—" if L >= 3 else ("待公布" if a["addon"] is None else yuan(a["addon"]))
            parts.append(f"<tr><td>{LEVEL_CN[L]}</td><td>{conds[L]}</td><td>{base}</td><td>{add}</td></tr>")
        parts.append(f"</table><div class='muted'>固定奖档位：{amts['_tier_note']}（按上期开奖后奖池判断）；* 为按规则推算，官方公布后自动替换。数据来源：{e('、'.join(d.get('sources') or [d.get('source','')]))}</div></div>")
    if not results:
        parts.append("<div class='card'>还没有录入号码，无法对奖。<br><span class='muted'>用 <code>python3 dlt10.py add</code> 或 <code>import</code> 录入追号计划。</span></div>")
    ts = sum(r["spent"] for r in results); tw = sum(r["won"] for r in results)
    if results:
        parts.append(f"<div class='card'><b>全部计划合计</b><div class='stats'>"
                     f"<div class='stat'><div class='v'>{yuan(ts)}</div><div class='k'>已投入(元)</div></div>"
                     f"<div class='stat'><div class='v'>{yuan(tw)}</div><div class='k'>中奖(元)</div></div>"
                     f"<div class='stat'><div class='v {'pos' if tw-ts>=0 else 'neg'}'>{tw-ts:+,}</div><div class='k'>净额(元)</div></div></div></div>")
    for r in results:
        pct = int(100 * r["done"] / max(1, r["periods"]))
        parts.append(f"<div class='card'><div style='display:flex;justify-content:space-between;flex-wrap:wrap'><b>{e(r['name'])}</b>"
                     f"<span class='muted'>{r['n_tickets']}注/期 · {'追加' if r['addon'] else '不追加'} · 每期 {r['cost_per_period']} 元 · 起始第{r['start_issue']}期</span></div>"
                     f"<div style='margin:8px 0 4px'>进度 <b>{r['done']}/{r['periods']}</b> 期</div><div class='bar'><i style='width:{pct}%'></i></div>"
                     f"<div class='stats'><div class='stat'><div class='v'>{yuan(r['spent'])}</div><div class='k'>已投入 / 计划 {yuan(r['total_cost'])}</div></div>"
                     f"<div class='stat'><div class='v'>{yuan(r['won'])}</div><div class='k'>中奖(元)</div></div>"
                     f"<div class='stat'><div class='v {'pos' if r['net']>=0 else 'neg'}'>{r['net']:+,}</div><div class='k'>净额(元)</div></div></div>")
        if not r["rows"]:
            parts.append(f"<div class='muted'>尚未开奖，等待第{r['start_issue']}期。</div>")
        for k, row in enumerate(reversed(r["rows"])):
            if row.get("unsupported"):
                parts.append(f"<details><summary>第{row['issue']}期 <span class='tag'>旧规则期，未对奖</span></summary></details>")
                continue
            nwin = sum(1 for t in row["tickets"] if t["level"])
            tag = (f"<span class='tag win'>中 {nwin} 注 · {yuan(row['prize'])} 元{'+待公布' if row['pending'] else ''}</span>" if nwin
                   else "<span class='tag'>未中奖</span>")
            parts.append(f"<details{' open' if k == 0 else ''}><summary><span>第{row['issue']}期 <span class='muted'>{e(row.get('date') or '')}</span></span>"
                         f"<span>{balls(row['draw_front'],'df')} {balls(row['draw_back'],'db')}</span>{tag}</summary>")
            for i, t in enumerate(row["tickets"], 1):
                if t["level"]:
                    amt = "待公布" if t["pending"] else f"{yuan(t['prize'])} 元" + (f"（含追加 {yuan(t['addon'])}{'，推算' if t['est'] else ''}）" if t["addon"] else "")
                    lt = f"<span class='tag {'big' if t['level'] <= 3 else 'win'}'>{t['level_cn']} · {amt}</span>"
                else:
                    lt = "<span class='tag'>未中</span>"
                parts.append(f"<div class='zhu'><span><span class='muted'>第{i}注</span> {balls(t['front'],'f',t['front_hit'])} {balls(t['back'],'b',t['back_hit'])}</span>"
                             f"<span><span class='muted'>中{t['hit']}</span> {lt}</span></div>")
            parts.append("</details>")
        parts.append("</div>")
    parts.append("<div class='card muted'>规则：第26014期起新规则，7个奖级。一、二等奖为浮动奖（按官方公布单注奖金；追加另得基本奖金的80%）；"
                 "三～七等奖为固定奖：上期开奖后奖池&lt;8亿时 5000/300/150/15/5 元，≥8亿时 6666/380/200/18/7 元；追加不参与固定奖。每注2元，追加每注+1元。"
                 "每注只兑最高奖级。以官方兑奖结果为准。</div>")
    if fetch_log:
        parts.append("<div class='card muted'>抓取日志：<br>" + "<br>".join(e(x) for x in fetch_log) + "</div>")
    parts.append("<footer>dlt10 · 本地生成 · 理性购彩</footer></div></body></html>")
    return "".join(parts)


def write_outputs(plans, cache, fetch_log=None, fetch_ok=True):
    results = evaluate(plans, cache)
    save_json(RESULTS_FILE, {"generated": now_str(), "results": results})
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(render_html(results, cache, fetch_log))
    s = text_summary(results, cache, fetch_ok)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(s + "\n")
    return results, s


# ───────────────────────── 命令 ─────────────────────────
def cmd_add(a):
    plans = load_plans()
    zhus = list(a.zhu or [])
    if a.zhu_file:
        with open(a.zhu_file, encoding="utf-8") as f:
            zhus += [ln for ln in f if re.search(r"\d", ln)]
    p = make_plan(plans, a.name, zhus, a.start, a.periods, a.addon, a.note or "")
    plans.append(p); save_json(PLANS_FILE, plans)
    print(f"✅ 已添加计划 #{p['id']} {p['name']}：{len(p['tickets'])} 注/期 × {p['periods']} 期，"
          f"{'追加' if p['addon'] else '不追加'}，每期 {plan_cost_per_period(p)} 元，共 {plan_cost_per_period(p)*p['periods']} 元，起始第{p['start_issue']}期")
    for i, t in enumerate(p["tickets"], 1):
        print(f"   第{i}注 {fmt_nums(t['front'])} + {fmt_nums(t['back'])}")


def cmd_list(a):
    plans = load_plans(); cache = load_json(DRAWS_FILE, {})
    if not plans:
        print("还没有追号计划（plans.json 为空）。用 add 或 import 添加。"); return
    for r in evaluate(plans, cache):
        print(f"#{r['id']} {r['name']}  {r['n_tickets']}注/期 {'追加' if r['addon'] else '不追加'}  起始{r['start_issue']}  "
              f"进度 {r['done']}/{r['periods']}  已投入 {r['spent']} 元  中奖 {r['won']} 元  净额 {r['net']:+} 元")
        for row in r["rows"]:
            if row.get("unsupported"):
                print(f"   {row['issue']}  旧规则期，未对奖"); continue
            s = "  ".join(f"{t['hit']}" + (f"[{t['level_cn']}{'' if not t['pending'] else '待公布'}]" if t["level"] else "") for t in row["tickets"])
            print(f"   {row['issue']} {row.get('date','')} 开 {fmt_nums(row['draw_front'])}+{fmt_nums(row['draw_back'])} | {s} | 奖金 {row['prize']}")


def cmd_run(a):
    plans = load_plans()
    try:
        cache, log = update_draws(plans, verbose=not a.quiet)
        ok = any(x.startswith("✅") for x in log)
    except Exception as ex:
        cache, log, ok = load_json(DRAWS_FILE, {}), [f"❌ 抓取异常：{ex}"], False
    _, s = write_outputs(plans, cache, log, ok)
    print("\n" + s)


def cmd_report(a):
    _, s = write_outputs(load_plans(), load_json(DRAWS_FILE, {}))
    print(s)


def cmd_draws(a):
    cache = load_json(DRAWS_FILE, {})
    for i in sorted(cache, key=int, reverse=True)[: a.n]:
        d = cache[i]
        print(f"{i} {d.get('date','')} {fmt_nums(d['front'])} + {fmt_nums(d['back'])}  奖池 {d.get('pool',0):,}  [{d.get('source','')}]")


def cmd_remove(a):
    plans = load_plans(); n = len(plans)
    plans = [p for p in plans if str(p["id"]) != str(a.id) and p["name"] != a.id]
    save_json(PLANS_FILE, plans); print(f"已删除 {n-len(plans)} 个计划")


def cmd_import(a):
    plans = load_plans(); added = []
    path = a.file
    if path.lower().endswith(".json"):
        data = load_json(path, [])
        items = data.get("plans", data) if isinstance(data, dict) else data
        for it in items:
            zhus = it.get("tickets") or it.get("zhu") or it.get("lines")
            p = make_plan(plans + added, it["name"], zhus, it.get("start_issue") or it.get("start"),
                          it.get("periods", 10), parse_bool(it.get("addon", False)), it.get("note", ""))
            added.append(p)
    else:
        # CSV 列：name,start_issue,periods,addon,front,back（每行一注；同名行合并为同一计划）
        groups = {}
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                g = groups.setdefault(row["name"].strip(), {"start": row.get("start_issue") or row.get("start"),
                                                            "periods": row.get("periods") or 10,
                                                            "addon": parse_bool(row.get("addon", "")), "zhus": []})
                g["zhus"].append(f"{row['front']} + {row['back']}")
        for name, g in groups.items():
            added.append(make_plan(plans + added, name, g["zhus"], str(g["start"]).strip(), int(g["periods"]), g["addon"]))
    for i, p in enumerate(added):
        p["id"] = new_plan_id(plans) + i
    plans += added; save_json(PLANS_FILE, plans)
    print(f"✅ 导入 {len(added)} 个计划：" + "、".join(f"{p['name']}({len(p['tickets'])}注×{p['periods']}期)" for p in added))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dlt10.py", description="大乐透 10期对奖助手")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add", help="新建追号计划")
    s.add_argument("--name", required=True, help="计划名称")
    s.add_argument("--zhu", action="append", help='一注号码，如 "01 05 10 22 33 + 03 09"，可重复多次')
    s.add_argument("--zhu-file", help="每行一注的文本文件")
    s.add_argument("--start", required=True, help="起始期号，如 26119")
    s.add_argument("--periods", type=int, default=10, help="追号期数，默认 10")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--addon", dest="addon", action="store_true", help="追加投注（每注 3 元）")
    g.add_argument("--no-addon", dest="addon", action="store_false", help="不追加（默认，每注 2 元）")
    s.set_defaults(addon=False)
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_add)
    sub.add_parser("list", help="查看计划进度").set_defaults(fn=cmd_list)
    s = sub.add_parser("run", help="抓取开奖并对奖"); s.add_argument("-q", "--quiet", action="store_true"); s.set_defaults(fn=cmd_run)
    sub.add_parser("report", help="仅用本地缓存重新生成报告").set_defaults(fn=cmd_report)
    s = sub.add_parser("import", help="从 JSON/CSV 导入计划"); s.add_argument("file"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("draws", help="查看缓存开奖"); s.add_argument("-n", type=int, default=10); s.set_defaults(fn=cmd_draws)
    s = sub.add_parser("remove", help="删除计划（id 或名称）"); s.add_argument("id"); s.set_defaults(fn=cmd_remove)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except ValueError as ex:
        print(f"❌ {ex}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    main()
