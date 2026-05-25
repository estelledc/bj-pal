"""BJ-Pal tool layer：Planner / Replanner 通过这里调用底层数据。

模块边界：
- amap_search   — POI 检索（按片区 / 类目 / 半径 / 营业时间 / 预算）
- ugc_signals   — UGC aspect 切片（环境/拥堵/排队/价格 hint/适配场景 ...）
- availability_probe — mock 余位 / 排队检测（W1 D5 实现）
- mock_book / mock_message — mock 下单 / IM（W2 D1 实现）
"""
