# -*- coding: utf-8 -*-
"""用真实历史开奖（第26105–26118期，来源 500彩票网，已与甘肃体彩网/旧缓存交叉核对）验证对奖与奖金逻辑。
本文件中的“示例”计划仅用于测试，不是用户的真实号码。"""
import json, os, sys, tempfile, unittest, csv
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import dlt10 as D

DRAWS = json.load(open(os.path.join(HERE, "fixtures", "draws_real_26105_26118.json"), encoding="utf-8"))


def tk(s):
    f, b = D.parse_zhu(s); return {"front": f, "back": b}


def chk(issue, zhu, addon=False, cache=None):
    cache = cache or DRAWS
    d = cache[issue]; t = tk(zhu)
    return D.check_zhu(t["front"], t["back"], d, D.level_amounts(d, cache), addon)


class TestRules(unittest.TestCase):
    def test_level_table_new_rules(self):
        exp = {(5,2):1,(5,1):2,(5,0):3,(4,2):3,(4,1):4,(4,0):5,(3,2):5,(3,1):6,(2,2):6,
               (3,0):7,(2,1):7,(1,2):7,(0,2):7,(2,0):0,(1,1):0,(1,0):0,(0,1):0,(0,0):0}
        for k, v in exp.items():
            self.assertEqual(D.prize_level(*k), v, k)

    def test_real_draw_numbers(self):
        self.assertEqual(DRAWS["26118"]["front"], [2, 9, 21, 26, 32]); self.assertEqual(DRAWS["26118"]["back"], [6, 12])
        self.assertEqual(DRAWS["26117"]["front"], [4, 13, 19, 29, 31]); self.assertEqual(DRAWS["26117"]["back"], [2, 12])
        self.assertEqual(DRAWS["26113"]["front"], [2, 4, 6, 10, 11]); self.assertEqual(DRAWS["26113"]["back"], [1, 10])


class TestRealDraws(unittest.TestCase):
    # 26113：上期(26112)奖池 7.99 亿 < 8 亿 → 固定奖低档；官方一等奖 2,392,344，追加 1,913,875；二等奖 22,428，追加 17,942
    def test_26113_first_prize_with_addon(self):
        r = chk("26113", "02 04 06 10 11 + 01 10", addon=True)
        self.assertEqual((r["hit"], r["level"], r["base"], r["addon"], r["prize"]), ("5+2", 1, 2392344, 1913875, 4306219))
        r = chk("26113", "02 04 06 10 11 + 01 10", addon=False)
        self.assertEqual(r["prize"], 2392344)

    def test_26113_second_prize(self):
        r = chk("26113", "02 04 06 10 11 + 01 12", addon=True)
        self.assertEqual((r["level"], r["base"], r["addon"]), (2, 22428, 17942))

    def test_26113_fixed_prizes_low_tier_addon_ignored(self):
        self.assertEqual(chk("26113", "02 04 06 10 11 + 03 05", True)["prize"], 5000)   # 5+0 三等奖，追加不加钱
        self.assertEqual(chk("26113", "02 04 06 10 30 + 01 10", True)["prize"], 5000)   # 4+2 三等奖
        self.assertEqual(chk("26113", "02 04 06 10 30 + 01 05")["prize"], 300)          # 4+1
        self.assertEqual(chk("26113", "02 04 06 10 30 + 03 05")["prize"], 150)          # 4+0
        self.assertEqual(chk("26113", "02 04 06 20 30 + 01 10")["prize"], 150)          # 3+2
        self.assertEqual(chk("26113", "02 04 06 20 30 + 01 05")["prize"], 15)           # 3+1
        self.assertEqual(chk("26113", "02 04 16 20 30 + 01 10")["prize"], 15)           # 2+2
        self.assertEqual(chk("26113", "02 04 06 20 30 + 03 05")["prize"], 5)            # 3+0
        self.assertEqual(chk("26113", "02 04 16 20 30 + 01 05")["prize"], 5)            # 2+1
        self.assertEqual(chk("26113", "02 14 16 20 30 + 01 10")["prize"], 5)            # 1+2
        self.assertEqual(chk("26113", "12 14 16 20 30 + 01 10")["prize"], 5)            # 0+2
        self.assertEqual(chk("26113", "02 04 16 20 30 + 03 05")["prize"], 0)            # 2+0 不中

    # 26112：上期(26111)奖池 8.20 亿 ≥ 8 亿 → 固定奖高档 6666/380/200/18/7
    def test_26112_high_tier(self):
        self.assertEqual(chk("26112", "01 03 05 10 30 + 03 09")["prize"], 6666)
        self.assertEqual(chk("26112", "01 03 05 10 30 + 03 11")["prize"], 380)
        self.assertEqual(chk("26112", "01 03 05 20 30 + 03 11")["prize"], 18)
        self.assertEqual(chk("26112", "01 03 05 20 30 + 04 11")["prize"], 7)
        self.assertEqual(chk("26112", "11 13 15 20 30 + 03 09")["prize"], 7)

    def test_26112_first_prize_addon_estimated_when_no_official_addon_winner(self):
        # 26112 官方一等奖追加中出 0 注（金额 0），按规则第十六条推算 = round(8,047,843 × 0.8)
        r = chk("26112", "01 03 05 10 22 + 03 09", addon=True)
        self.assertEqual((r["base"], r["addon"], r["est"]), (8047843, 6438274, True))

    def test_tier_inferred_from_previous_pool_matches_official(self):
        # 去掉官方固定奖金额，只靠上期奖池推断，结果应与官方一致（26106低/26107高/26112高/26113低）
        for iss in ["26106", "26107", "26108", "26109", "26110", "26111", "26112", "26113", "26114", "26117"]:
            c = json.loads(json.dumps(DRAWS))
            official = {L: c[iss]["levels"][str(L)]["amount"] for L in range(3, 8)}
            for L in range(3, 8):
                c[iss]["levels"][str(L)].pop("amount")
            a = D.level_amounts(c[iss], c)
            self.assertEqual({L: a[L]["base"] for L in range(3, 8)}, official, iss)

    def test_26118_pending_floating_but_fixed_known(self):
        # 26118（2026-10-17）刚开奖，浮动奖未公布 → 待公布；固定奖按 26117 奖池 7.46 亿推断低档
        r = chk("26118", "02 09 21 26 32 + 06 01")
        self.assertTrue(r["pending"]); self.assertEqual(r["level"], 2)
        r = chk("26118", "02 09 21 16 17 + 06 01")
        self.assertEqual((r["level"], r["prize"]), (6, 15))


class TestPlans(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for k, n in [("PLANS_FILE", "plans.json"), ("DRAWS_FILE", "draws.json"), ("RESULTS_FILE", "results.json"),
                     ("REPORT_FILE", "report.html"), ("SUMMARY_FILE", "summary.txt")]:
            setattr(D, k, os.path.join(self.tmp, n))

    def example_plan(self):
        # 示例（测试专用，非用户号码）
        return D.make_plan([], "示例计划", ["01 03 05 10 22 + 03 09", "02 04 06 20 30 + 01 05", "33 34 35 07 08 + 11 12"],
                           "26110", 10, True)

    def test_validation(self):
        for bad in ["01 02 03 04 + 01 02", "01 02 03 04 05 06 + 01 02", "01 01 03 04 05 + 01 02",
                    "01 02 03 04 36 + 01 02", "01 02 03 04 05 + 01 13", "01 02 03 04 05 + 03 03", "00 02 03 04 05 + 01 02"]:
            with self.assertRaises(ValueError, msg=bad):
                D.parse_zhu(bad)
        self.assertEqual(D.parse_zhu("5,1,10,22,33|9,3"), ([1, 5, 10, 22, 33], [3, 9]))
        self.assertEqual(D.parse_zhu("01 05 10 22 33 03 09"), ([1, 5, 10, 22, 33], [3, 9]))
        with self.assertRaises(ValueError):
            D.make_plan([], "x", ["01 02 03 04 05 + 01 02"], "2611")

    def test_example_plan_progress_cost_and_prizes(self):
        p = self.example_plan()
        r = D.evaluate([p], DRAWS)[0]
        self.assertEqual([row["issue"] for row in r["rows"]], [str(i) for i in range(26110, 26119)])
        self.assertEqual((r["done"], r["periods"], r["cost_per_period"], r["spent"], r["total_cost"]), (9, 10, 9, 81, 90))
        row = {x["issue"]: x for x in r["rows"]}
        # 26112：注1 = 当期开奖号 → 一等奖 8,047,843 + 追加推算 6,438,274；注2 02 04 06 20 30+01 05 → 0+0；注3 → 0+0
        self.assertEqual([t["level"] for t in row["26112"]["tickets"]], [1, 0, 0])
        self.assertEqual(row["26112"]["prize"], 8047843 + 6438274)
        # 26113：注1 01 03 05 10 22+03 09 vs 02 04 06 10 11+01 10 → 1+0 不中；注2 → 3+1 六等奖 15；注3 → 0+0
        self.assertEqual([t["hit"] for t in row["26113"]["tickets"]], ["1+0", "3+1", "0+0"])
        self.assertEqual(row["26113"]["prize"], 15)
        # 26118 浮动奖未公布：本计划无 1/2 等奖命中则无 pending
        self.assertEqual(r["won"], sum(x["prize"] for x in r["rows"]))
        self.assertEqual(r["net"], r["won"] - 81)

    def test_cost_no_addon(self):
        p = D.make_plan([], "示例2", ["01 02 03 04 05 + 01 02"] * 5, "26119", 10, False)
        self.assertEqual(D.plan_cost_per_period(p) * 10, 100)   # 5注 × 2元 × 10期
        r = D.evaluate([p], DRAWS)[0]
        self.assertEqual((r["done"], r["spent"]), (0, 0))

    def test_import_csv_and_json_and_outputs(self):
        csvp = os.path.join(self.tmp, "in.csv")
        with open(csvp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f); w.writerow(["name", "start_issue", "periods", "addon", "front", "back"])
            w.writerow(["示例CSV", "26112", "10", "是", "01 03 05 10 22", "03 09"])
            w.writerow(["示例CSV", "26112", "10", "是", "02 04 06 10 11", "01 10"])
        D.cmd_import(type("A", (), {"file": csvp})())
        jp = os.path.join(self.tmp, "in.json")
        json.dump({"plans": [{"name": "示例JSON", "start_issue": "26117", "addon": False,
                              "tickets": ["04 13 19 29 31 + 02 12", {"front": [1, 2, 3, 4, 5], "back": [6, 7]}]}]},
                  open(jp, "w", encoding="utf-8"), ensure_ascii=False)
        D.cmd_import(type("A", (), {"file": jp})())
        plans = D.load_plans()
        self.assertEqual([(p["id"], p["name"], len(p["tickets"]), p["addon"]) for p in plans],
                         [(1, "示例CSV", 2, True), (2, "示例JSON", 2, False)])
        res, s = D.write_outputs(plans, DRAWS)
        self.assertIn("进度 7/10 期", s); self.assertIn("进度 2/10 期", s)
        self.assertIn("第26118期", s)
        html = open(D.REPORT_FILE, encoding="utf-8").read()
        self.assertIn("示例CSV", html); self.assertIn("ball f hit", html); self.assertIn("一等奖", html)
        # 示例JSON 第26117期 注1 = 当期号码 → 一等奖 5,834,813（不追加）
        r2 = res[1]; first = r2["rows"][0]
        self.assertEqual((first["issue"], first["tickets"][0]["level"], first["tickets"][0]["prize"]), ("26117", 1, 5834813))


class TestOfficialParser(unittest.TestCase):
    def test_parse_official_sample(self):
        data = json.load(open(os.path.join(HERE, "fixtures", "official_sample_21061.json"), encoding="utf-8"))
        d = D.parse_official(data)[0]
        self.assertEqual((d["issue"], d["date"], d["front"], d["back"], d["pool"]),
                         ("21061", "2021-05-31", [15, 19, 20, 26, 32], [4, 7], 502877803))
        self.assertEqual(d["levels"]["1"], {"amount": 6484687, "count": 7, "addon_amount": 5187749, "addon_count": 3})
        self.assertEqual(d["levels"]["4"]["count"], 1032)


if __name__ == "__main__":
    unittest.main(verbosity=2)
