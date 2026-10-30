# 大乐透 10期对奖助手（dlt10）

纯 Python 标准库，运行在 Linux box（不依赖用户 Mac）。

    cd /workspace/dlt10
    python3 dlt10.py add --name "我的追号" --start 26119 --addon \
        --zhu "01 05 10 22 33 + 03 09" --zhu "02 08 15 27 30 + 04 11"   # 可多次 --zhu；默认 10 期、不追加
    python3 dlt10.py import plans.csv      # CSV 列：name,start_issue,periods,addon,front,back（每行一注）
    python3 dlt10.py import plans.json     # [{"name","start_issue","periods","addon","tickets":["01 02 03 04 05 + 06 07",...]}]
    python3 dlt10.py run                   # 抓开奖 → 对奖 → results.json / report.html / summary.txt（微信摘要）
    python3 dlt10.py list | report | draws -n 10 | remove <id或名称>
    python3 tests/test_dlt10.py

开奖来源顺序：体彩官方 webapi.sporttery.cn（境外 IP 会被 EdgeOne 拦截 HTTP 567）→ 500彩票网历史表 + 单期详情（各奖级单注奖金）→ 甘肃体彩网（仅最新一期，兜底/交叉核对）。
奖级：第26014期起新规则 7 个奖级；三～七等固定奖按上期开奖后奖池 <8亿 / ≥8亿 分档；追加（+1元/注）仅一、二等奖，+80%。
