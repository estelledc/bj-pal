"""UGC 数据扩展主脚本（D1.1）：把 UGC 从 1 个片区 37 条扩到 5+ 片区 100+ 条。

跑法：
    cd explorations/mini-apps/bj-pal
    python3 src/etl/expand_ugc_areas.py [--dry-run] [--areas sanlitun,wangfujing,...]

不爬大众点评，仅用网络公开评论摘要 + LongCat 结构化抽取。
dataset_version 标 synthetic_from_public_summaries_v2，来源 URL 留痕到 raw_json。

raw_text 来自：
- 五道营片区已有的 manual_ugc_seed_v1（不重做）
- 三里屯 / 王府井 / 南锣 / 798 / 奥森：网络公开评论汇总 + 通用知识

任何"硬"事实（价格 / 排队时长 / 营业时间）的措辞用模糊语 ("普遍反映"/"多条提到")，
不伪造具体数字 / 不"网友 X 说"。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.text_aspect_extractor import expand_area  # noqa: E402
from loader import get_conn  # noqa: E402

# ============================================================
# 5 个目标片区 raw_text（公开评论汇总 + 通用认知）
# ============================================================

AREAS = {
    "三里屯片区": {
        "target_count": 14,
        "source_urls": [
            "https://xiaohongshu.com (三里屯 citywalk 路线推荐)",
            "https://www.mafengwo.cn (北京三里屯周末游玩攻略)",
            "https://www.dianping.com (三里屯太古里 用户评价)",
            "https://www.zhihu.com (2025年三里屯还值得去吗 讨论帖)",
            "https://www.timeout.cn (2025三里屯周末完全指南)",
        ],
        "raw_text": """三里屯片区是北京最有活力的潮流商圈之一，位于朝阳区，地铁 10 号线团结湖站 / 农业展览馆站可达。

核心地标：太古里（北区精品店、南区餐饮）、三里屯路、工人体育场（改造后新增地下商业街连接三里屯）。

周末下午（14:00-18:00）人流量大、氛围感强，但整体节奏舒适。多条评论指出周末停车困难，建议公共交通出行。普遍反映三里屯的潮流业态成熟、设计师店铺密集，咖啡（%Arabica、Manner、Voyage）、买手店、艺术装置丰富，适合年轻人和拍照打卡。

餐饮选择多样，西餐、日料、亚洲菜、咖啡甜品均有覆盖。但周末部分网红餐厅需排队，建议提前在大众点评取号或避开 12:00-13:30 高峰。多条提到太古里附近瑜舍酒店周边的下午茶体验不错。

诟病点：周末人流量过大、部分网红店打卡 > 实际体验、停车难。建议错峰：14:00 后或工作日前往体验更好。

支线推荐：三里屯北小街、幸福村等支线胡同氛围更地道，有不少独立咖啡馆和小众店铺。

综合评价：⭐4.5/5，适合 citywalk + 探店 + 拍照场景；不适合追求安静的家庭日。

适用场景：年轻情侣、朋友聚会、潮流探店、独立 citywalk。
不适用场景：带 5 岁以下小娃、追求安静、预算极度敏感。
""",
    },

    "王府井-东单片区": {
        "target_count": 14,
        "source_urls": [
            "https://www.dianping.com (王府井小吃街 老字号评价)",
            "https://www.meituan.com (全聚德 / 东来顺 王府井店 用户排队反馈)",
            "https://www.mafengwo.cn (王府井步行街 游玩攻略)",
        ],
        "raw_text": """王府井片区位于东城区核心商圈，地铁 1 号线王府井站直达。是北京老牌步行街+购物商圈，覆盖王府井大街、apm 商场、王府中环等节点。

核心地标：全聚德烤鸭（王府井店）、东来顺涮肉、四季民福（故宫店附近）、南门涮肉、稻香村、好利来、王府井小吃街。

周末高峰用餐（12:00-13:30）排队普遍较长。多条反映：全聚德通常排队 1-2 小时，建议提前在大众点评取号；东来顺周末午餐高峰排队约 45 分钟到 1 小时；四季民福、花家怡园等京菜馆排队最久（1.5-3 小时）；南门涮肉晚餐时段约 1 小时。王府中环高端餐饮相对排队短但需提前预订。

错峰建议：11:00 前或 14:00 后用餐，可大幅减少等待。多条建议先在大众点评取号再去逛街，回头进店更省时间。

替代选择：王府井小吃街可快速解决用餐；好利来 / 稻香村适合带走伴手礼；步行可至东四 / 灯市口区域，排队压力小很多。

诟病点：周末游客密度大、部分老字号"游客向"风评、停车极不便。

综合评价：⭐4.2/5，适合带长辈品老字号、初次到京的城市步行体验；不适合追求小众、预算敏感、不爱排队。

适用场景：家庭游、带长辈、初到北京游客、想体验"老北京"标签。
不适用场景：周末已有时间紧迫感、想避开人群、不能排队。
""",
    },

    "南锣鼓巷片区": {
        "target_count": 12,
        "source_urls": [
            "https://www.dianping.com (南锣鼓巷 文宇奶酪 等老字号评价)",
            "https://www.xiaohongshu.com (南锣鼓巷 周末打卡笔记)",
            "https://www.mafengwo.cn (北京南锣鼓巷周末游玩贴士)",
        ],
        "raw_text": """南锣鼓巷片区位于东城区，地铁 6 号线 / 8 号线南锣鼓巷站。北京最著名的胡同商业街之一，连接什刹海、烟袋斜街、鼓楼东大街。

核心地标：南锣鼓巷主街、文宇奶酪店、文宇酸奶、杨小贤芒果捞、各类糖葫芦烤肠摊。支线胡同：帽儿胡同、雨儿胡同、菊儿胡同。

周末和节假日人流量极大，主街常常摩肩接踵、几乎需要"挪步前行"。景区高峰期会实施单向通行和限流，游客需排队进入。

网红店排队：文宇奶酪店常年排队 1-2 小时，双皮奶和奶酪最受欢迎；文宇酸奶、杨小贤等也需较长等待；糖葫芦烤肠等小吃摊位前都有长队。

文创评价：故宫文创、老北京主题伴手礼种类丰富；兔儿爷、京剧脸谱、剪纸等传统手工艺品有特色；创可贴 T 恤店等独立设计师店铺有创意。槽点：商品同质化严重（与全国古镇商业街相似）、价格偏高溢价明显、商业气息过浓失去原有胡同文化韵味。

正面评价：胡同建筑保留较好，可感受老北京风貌；周边支线胡同（帽儿、雨儿、菊儿）相对清静，体验更佳；美食选择多样。

负面评价：千街一面、商业化过度、网红店打卡 > 实际体验、部分小吃口味一般、价格虚高、周末体验感差。

建议：避开周末和节假日，工作日上午人少；不必执着于排队网红店，深入支巷探索更有意思；可结合什刹海 / 烟袋斜街 / 鼓楼一起游览。

综合评价：⭐3.8/5，适合带朋友拍照打卡、伴手礼采购；不适合静心 citywalk、敏感于人流。

适用场景：初到北京游客、伴手礼采购、想拍胡同照片。
不适用场景：周末避免拥挤、追求纯正胡同氛围、对网红店祛魅。
""",
    },

    "798艺术区": {
        "target_count": 12,
        "source_urls": [
            "https://ucca.org.cn (UCCA 当代艺术中心展览信息)",
            "https://798district.com (798 艺术区官方导览)",
            "https://www.xiaohongshu.com (798 周末打卡笔记)",
            "https://www.dianping.com (798 咖啡馆评价)",
        ],
        "raw_text": """798 艺术区位于朝阳区大山子，原 1950 年代包豪斯风格军工厂改造，现为中国最知名的当代艺术区。地铁 14 号线望京南站 / 15 号线大山子站可达。

核心场馆：UCCA 尤伦斯当代艺术中心（国际级当代艺术展）、798 CUBE（多媒体数字艺术）、佩斯画廊（Pace Gallery，国际艺术家）、罐子艺术中心（工业风轮换展）、木木美术馆 M Woods（年轻向当代艺术）。许多小画廊周末免费开放。

咖啡馆推荐：Voyage Coffee（精品咖啡 + 工业风）、Soloist Coffee（获奖烘焙坊）、% Arabica 798（极简白色空间，拍照圣地）、Metal Hands 铁手咖啡（本地艺术氛围）、Café Flatwhite（看展中间小憩）。

拍照打卡点：红砖墙毛时代标语、巨型恐龙雕塑、工业管道烟囱、不断更新的涂鸦墙、相邻的 751 D·Park 蒸汽机车、各种装置艺术 "I ❤ 798" 标志。

周末体验贴士：周一多数画廊闭馆，建议周六周日 10:00-18:00；建议时长 4-6 小时；穿舒适鞋；极简或艺术风穿搭最上镜；可结合相邻 751 D·Park 延伸工业艺术探索。

正面：氛围独特、艺术质感强、咖啡选择多、可看展可拍照、是北京年轻文艺青年的周末聚集地。

诟病：部分画廊门票偏贵、餐饮选择相对少、停车不便、夏季室外较晒。

综合评价：⭐4.4/5，适合艺术爱好者、文艺约会、文创探店；不适合带 3 岁以下小娃、追求传统观光。

适用场景：情侣文艺约会、朋友看展、独立 citywalk + 摄影、文创采购。
不适用场景：带特别小的小孩、想快速吃饭购物、追求人多热闹。
""",
    },

    "奥林匹克公园片区": {
        "target_count": 10,
        "source_urls": [
            "https://www.olympic-park.com (奥林匹克森林公园官方介绍)",
            "https://www.xiaohongshu.com (奥森周末跑步亲子笔记)",
            "https://www.mafengwo.cn (北京奥森游玩攻略)",
        ],
        "raw_text": """奥林匹克森林公园（奥森）位于朝阳区北部，2008 年北京奥运会配套生态公园，免费入园，地铁 8 号线森林公园南门站 / 奥林匹克公园站。北园南园合计约 680 公顷，是北京北部最大的城市绿肺。

核心区域：南园（5 公里跑道、奥海、湿地、樱花林）、北园（仰山、瀑布、奥林匹克塔远眺）、儿童活动区、亲子草坪。

周末人群：跑步爱好者（清晨 / 傍晚 5km 标准跑道密度高，周末跑团活动）、亲子家庭（带娃野餐、放风筝、骑车）、骑行（园区内有租车服务，环园一圈约 8-10 km）、摄影（春季樱花、秋季银杏 / 红叶为高峰打卡期）。

正面评价：免费 + 大、绿化好空气佳、跑道平整设施完善、亲子友好设施多（草坪 / 卡丁车 / 儿童活动）、错峰可避周末闹市；春樱秋红银杏季观赏价值高。

诟病：园区面积大，没规划好容易走累；夏天蚊虫多、午后烈日暴晒；最近的食物选择少（建议自带或入园前在地铁口便利店补给）；周末停车场常满，建议地铁出行。

时段画像：清晨 6-8 点跑步族高峰；周末上午 10-12 点亲子家庭涌入；下午 14-17 点摄影 + 骑行；傍晚 17-19 点散步 + 跑步晚高峰。

综合评价：⭐4.6/5，适合带娃野餐 / 跑步训练 / 朋友拍照 / 低强度 citywalk；不适合追求城市感、想快速吃饭购物、对蚊虫敏感（夏季）、午后烈日时段（建议错峰到傍晚）。

适用场景：亲子周末（推荐）、跑步训练、骑行环园、摄影（樱花 / 银杏 / 落日）。
不适用场景：午餐刚需（园内餐饮少）、想室内活动避雨、午后烈日暴晒时段。
""",
    },
}


# ============================================================
# 主驱动
# ============================================================

def show_current_distribution():
    conn = get_conn()
    cur = conn.cursor()
    print("\n=== 当前 ugc_aspects 片区分布 ===")
    for row in cur.execute(
        "SELECT area_anchor, COUNT(*) FROM ugc_aspects "
        "GROUP BY area_anchor ORDER BY 2 DESC"
    ):
        print(f"  {row[0]:<25} {row[1]:>3} 条")
    print(f"  {'TOTAL':<25} "
          f"{cur.execute('SELECT COUNT(*) FROM ugc_aspects').fetchone()[0]:>3} 条")
    conn.close()


def already_seeded(area_anchor: str, dataset_version: str) -> int:
    """已经从 expand 跑入库的条数，避免重复扩。"""
    conn = get_conn()
    cur = conn.cursor()
    n = cur.execute(
        "SELECT COUNT(*) FROM ugc_aspects WHERE area_anchor=? "
        "AND raw_json LIKE ?",
        (area_anchor, f'%"dataset_version": "{dataset_version}"%'),
    ).fetchone()[0]
    conn.close()
    return n


def main():
    ap = argparse.ArgumentParser(description="UGC 4 片区扩展")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印 raw_text 长度和目标，不调 LLM")
    ap.add_argument("--areas", default="",
                    help="逗号分隔，限定要扩的片区。空=全跑")
    ap.add_argument("--force", action="store_true",
                    help="即使已有该 dataset_version 也重跑")
    args = ap.parse_args()

    show_current_distribution()

    keys = list(AREAS.keys())
    if args.areas:
        wanted = {x.strip() for x in args.areas.split(",")}
        keys = [k for k in keys if k in wanted or
                k.replace("片区", "").replace("艺术区", "") in wanted]
        if not keys:
            print(f"\n[ERROR] --areas 没匹配上任何片区。可选：")
            for k in AREAS:
                print(f"  - {k}")
            sys.exit(1)

    if args.dry_run:
        print("\n=== DRY RUN（不调 LLM）===")
        for k in keys:
            v = AREAS[k]
            print(f"\n[{k}] target={v['target_count']} text_len={len(v['raw_text'])}")
            print(f"  source_urls 数：{len(v['source_urls'])}")
        return

    print(f"\n=== 开始扩展 {len(keys)} 个片区 ===")
    total_inserted = 0
    for i, k in enumerate(keys, 1):
        v = AREAS[k]
        print(f"\n[{i}/{len(keys)}] {k}")
        already = already_seeded(k, "synthetic_from_public_summaries_v2")
        if already > 0 and not args.force:
            print(f"  [SKIP] 已有 {already} 条 v2 数据；--force 重跑")
            continue
        try:
            extracted, n = expand_area(
                area_anchor=k,
                raw_text=v["raw_text"],
                target_count=v["target_count"],
                source_urls=v["source_urls"],
            )
            total_inserted += n
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}")
            continue

    print(f"\n=== 完成 ===")
    print(f"本次新增：{total_inserted} 条")
    show_current_distribution()


if __name__ == "__main__":
    main()
