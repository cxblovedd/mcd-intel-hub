#!/usr/bin/env python3
"""
麦门人格画像生成器 - McPersona

从麦当劳 MCP 返回的历史订单中，提取四个可量化维度，
映射到一个轻松幽默的「麦门人格」，用于生成分享海报。

设计原则：
1. 所有维度必须由真实订单数据推导，不使用随机数。
2. 维度阈值经过真实样本验证（见 data/sample_orders.json）。
3. 画像结果仅为趣味性描述，不构成任何健康或营养建议。

用法:
    python3 persona_builder.py --orders orders.json
    python3 persona_builder.py --orders orders.json --points '{"availablePoint":"485.7","accumulativePoint":"2265.1"}'
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ── 口味偏好词库 ─────────────────────────────────────────────
# 依据麦当劳真实菜单品类划分，命中即计分（每个商品只归入首个命中类别，避免重复计数）
TASTE_RULES = {
    '甜品饮料': ['麦旋风', '圆筒', '新地', '派', '布丁', '圣代', '奶昔',
                '可乐', '雪碧', '咖啡', '奶铁', '茶', '豆浆', '橙', '奶昔'],
    '鸡腿风味': ['鸡腿堡', '板烧', '脆汁鸡', '麦辣鸡腿', '叫了个鸡', '辣味'],
    '深海鱼类': ['鳕鱼', '鱼排', '麦香鱼', '海苔'],
    '牛肉重口': ['巨无霸', '双层', '安格斯', '培根', '吉士汉堡', '芝士'],
    '麦满分早餐': ['麦满分', '猪柳蛋', '火腿扒', '蛋堡', '油条', '豆浆'],
    '小食组合': ['薯条', '麦乐鸡', '鸡翅', '鸡块', '脆薯饼', '小食', '玉米杯'],
    '联名收藏': ['联名', '盲盒', '手办', '周边', '钥匙扣', 'T恤', '帽'],
    '酱香重口': ['韩式', '辣椒', '芝士棒', '蘸酱', '秘制', '酱'],
}

# ── 核心人格定义（由口味主导决定）───────────────────────────
# key 需与 TASTE_RULES 的键对应
CORE_PERSONAS = {
    '甜品饮料': ('甜品判官', '冰淇淋和麦旋风是你续命的理由'),
    '鸡腿风味': ('炸鸡教父', '你的麦门生涯绕不开那一口酥脆鸡腿'),
    '深海鱼类': ('深海渔夫', '鱼系菜单的深度用户，鳕鱼是你的老朋友'),
    '牛肉重口': ('肉食暴君', '双层芝士从不手软，你对碳水的爱很坦荡'),
    '麦满分早餐': ('早餐教父', '早八人的命是麦满分给的'),
    '小食组合': ('小食囤家', '单点小食才是你的快乐源泉'),
    '联名收藏': ('联名猎人', '你来麦当劳，八成不全是为了吃'),
    '酱香重口': ('酱料研究员', '韩式辣椒黄油风味酱是你的本命'),
}

# ── 隐藏徽章定义（叠加在核心人格之上，可同时解锁多个）───────
# 条件由 build_persona 内的 badges 逻辑判定
HIDDEN_BADGES = {
    '全收集家': f'集齐全部 {len(TASTE_RULES)} 类口味，你在做数据田野调查',
    '打卡狂人': '消费横跨 8 个月以上，这是习惯不是偶然',
    '积分学徒': '累计消耗积分超过 5000，比店员还熟规则',
}

# 门店类型：1=餐厅 4=甜品站 5=得来速 6=团餐
BE_TYPE_LABEL = {
    '1': '堂食/外带餐厅',
    '2': '麦乐送外送',
    '4': '甜品站',
    '5': '得来速',
    '6': '企业团餐',
}


def flatten_items(orders):
    """展开订单中的所有商品（含套餐子项），返回商品名列表。"""
    items = []
    for order in orders:
        for prod in order.get('orderProductList') or []:
            name = prod.get('productName') or ''
            if name:
                items.append((name, prod.get('quantity', 1)))
            for sub in prod.get('comboItemList') or []:
                sname = sub.get('name') or ''
                if sname:
                    items.append((sname, sub.get('quantity', 1)))
    return items


def score_taste(items):
    """计算各口味维度得分。"""
    scores = {}
    matched_detail = defaultdict(list)
    for name, qty in items:
        for tag, keywords in TASTE_RULES.items():
            for kw in keywords:
                if kw in name:
                    scores[tag] = scores.get(tag, 0) + qty
                    matched_detail[tag].append(name)
                    break
    return scores, matched_detail


def score_rhythm(orders):
    """分析下单时间节律：早餐党 / 午餐党 / 下午茶党 / 夜宵党。"""
    slots = Counter()
    hour_detail = []
    for order in orders:
        try:
            ts = datetime.strptime(order.get('createTime', ''), '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            continue
        h = ts.hour
        hour_detail.append(h)
        if 6 <= h < 10:
            slots['早餐党'] += 1
        elif 10 <= h < 14:
            slots['午餐党'] += 1
        elif 14 <= h < 17:
            slots['下午茶党'] += 1
        elif 17 <= h < 21:
            slots['晚餐党'] += 1
        else:
            slots['深夜党'] += 1
    return slots, hour_detail


def analyze_geo(orders):
    """统计门店足迹：常去门店数、城市数、跨城指数。"""
    stores = {o.get('storeName', '') for o in orders if o.get('storeName')}
    cities = set()
    for store in stores:
        for city in ['厦门', '福州', '泉州', '南安', '北京', '上海', '广州', '深圳']:
            if city in store:
                cities.add(city)
                break
    return stores, cities


def build_persona(orders, points=None):
    """核心：根据订单数据推导麦门人格。

    判定优先级：
    1. 隐藏人格（全收集家 / 打卡狂人 / 积分学徒）
    2. 核心人格（口味主导，8 型）
    3. 数据不足则不给人格
    """
    valid = [o for o in orders if o.get('orderStatus') == '订单已完成']
    items = flatten_items(valid)

    taste_scores, taste_detail = score_taste(items)
    taste_counter = Counter(taste_scores)
    slots, hours = score_rhythm(valid)
    stores, cities = analyze_geo(valid)

    amounts = []
    for o in valid:
        try:
            amt = float(o.get('realTotalAmount') or 0)
            if amt > 0:
                amounts.append(amt)
        except (ValueError, TypeError):
            continue

    month_span = _month_span(valid)
    accumulative_point = 0.0
    if points:
        try:
            accumulative_point = float(points.get('accumulativePoint') or 0)
        except (ValueError, TypeError):
            accumulative_point = 0.0

    # ── 核心人格判定（始终给出，不被隐藏人格覆盖）──
    top_taste = taste_counter.most_common(2)
    MIN_ORDERS = 3
    if not top_taste or len(valid) < MIN_ORDERS:
        persona = '麦门新客'
        tagline_used = '你的麦门故事刚刚开始'
        desc_text = (f'目前只有 {len(valid)} 笔有效订单，'
                     f'再积累 {max(0, MIN_ORDERS - len(valid))} 笔就能解锁完整画像')
    else:
        primary = top_taste[0][0]
        persona, tagline_used = CORE_PERSONAS.get(
            primary, ('麦门常客', '均衡而稳定的选择')
        )
        desc_text = '、'.join(f'{tag} × {cnt}件' for tag, cnt in top_taste)

    # ── 隐藏人格判定（作为额外徽章叠加，不覆盖核心人格）──
    covered = len([k for k, v in taste_counter.items() if v > 0])
    badges = []
    if covered >= len(TASTE_RULES):
        badges.append(('麦门全收集家', HIDDEN_BADGES['全收集家']))
    if month_span >= 8:
        badges.append(('麦门打卡狂人',
                       HIDDEN_BADGES['打卡狂人'].replace(
                           '8 个月以上', f'{month_span} 个月')))
    if accumulative_point >= 5000:
        badges.append(('麦金学徒',
                       HIDDEN_BADGES['积分学徒'].replace(
                           '超过 5000', f'超过 {int(accumulative_point)}')))
    is_hidden = len(badges) > 0

    # ── 维度二：作息人格 ──
    if slots:
        top_slot = slots.most_common(1)[0][0]
        slot_desc = {
            '早餐党': '清晨的第一口麦门，是一天的正确打开方式',
            '午餐党': '午间补给站，稳定输出型选手',
            '下午茶党': '三点半的快乐，靠一杯麦咖啡续命',
            '晚餐党': '下班后的快乐据点，夜里最忙的那阵',
            '深夜党': '深夜食堂常客，夜晚才是你的黄金时段',
        }
        rhythm = slot_desc.get(top_slot, '')
    else:
        top_slot = '数据不足'
        rhythm = '时间数据不足，多点几单试试'

    # ── 维度三：足迹人格 ──
    store_count = len(stores)
    city_count = len(cities)
    if city_count >= 3:
        geo_tag = '跨城游侠'
        geo_desc = f'足迹遍布 {city_count} 座城市，{store_count} 家门店'
    elif store_count >= 3:
        geo_tag = '门店常驻客'
        geo_desc = f'常驻 {store_count} 家门店，认准那几个熟悉的味道'
    elif store_count >= 1:
        geo_tag = '社区守望者'
        geo_desc = f'你的麦门据点是 {store_count} 家门店'
    else:
        geo_tag = '数据不足'
        geo_desc = '门店数据不足'

    # ── 维度四：消费人格 ──
    paid_orders = len(amounts)

    if amounts:
        avg = sum(amounts) / len(amounts)
        if avg >= 60:
            spend_tag = '重度消费者'
            spend_desc = f'平均每单 ¥{avg:.0f}，出手相当阔绰'
        elif avg >= 30:
            spend_tag = '稳健型消费者'
            spend_desc = f'平均每单 ¥{avg:.0f}，花钱花得明白'
        elif avg >= 10:
            spend_tag = '轻量型消费者'
            spend_desc = f'平均每单 ¥{avg:.0f}，小满足就够了'
        else:
            spend_tag = '薅羊毛大师'
            spend_desc = f'平均每单 ¥{avg:.0f}，靠券活着，教程了'
    elif len(valid) > 0:
        spend_tag = '权益型消费者'
        spend_desc = '订单多为积分兑换或优惠，用权益把成本压得很低'
    else:
        spend_tag = '数据不足'
        spend_desc = '暂无实付订单，多点几单就能生成画像'

    return {
        'persona': persona,
        'tagline': tagline_used,
        'desc': desc_text,
        'isHidden': is_hidden,
        'badges': badges,
        'rhythm': {'tag': top_slot, 'desc': rhythm},
        'geo': {'tag': geo_tag, 'desc': geo_desc, 'stores': store_count, 'cities': city_count},
        'spend': {'tag': spend_tag, 'desc': spend_desc, 'avg': round(avg, 1) if amounts else 0,
                  'paidOrders': paid_orders},
        'taste': dict(taste_scores),
        'stats': {
            'totalOrders': len(orders),
            'validOrders': len(valid),
            'itemCount': sum(q for _, q in items),
            'storeCount': store_count,
            'cityCount': city_count,
            'monthSpan': month_span,
            'tasteCoverage': covered,
            'accumulativePoint': accumulative_point,
        },
    }


def _month_span(orders):
    """统计消费跨度。"""
    times = []
    for o in orders:
        try:
            times.append(datetime.strptime(o.get('createTime', ''), '%Y-%m-%d %H:%M:%S'))
        except (ValueError, TypeError):
            continue
    if not times:
        return 0
    days = (max(times) - min(times)).days
    return round(days / 30.4)


def render_card(persona, points=None):
    """渲染可分享的 Markdown 画像。

    设计原则：分享内容要一眼看懂。
    主信息只保留三块 —— 人格名、口味雷达、一行标签，
    其余维度收进折叠详情，避免阅读门槛。
    """
    s = persona['stats']
    top = sorted(persona['taste'].items(), key=lambda x: x[1], reverse=True)[:4]

    lines = []
    lines.append('# 你的麦门人格\n')
    lines.append('## ' + persona['persona'] + '\n')
    lines.append('> ' + persona['tagline'] + '\n')

    badges = persona.get('badges') or []
    if badges:
        badge_names = ' · '.join(n for n, _ in badges)
        lines.append(f'**隐藏徽章**：{badge_names}\n')

    # 口味雷达 —— 人格的主要依据，放前面
    if top:
        lines.append('### 你点的都是什么\n')
        for tag, cnt in top:
            bar = '█' * max(1, min(cnt, 12))
            lines.append(f'{tag} {cnt}件 {bar}')
        lines.append('')

    # 三个副标签压成一行，不展开解读；数据不足时整行省略
    tags = [persona['rhythm']['tag'], persona['geo']['tag'], persona['spend']['tag']]
    if '数据不足' not in tags:
        lines.append('**' + ' · '.join(tags) + '**\n')

    if s['validOrders'] > 0:
        lines.append(f"{s['validOrders']} 笔订单 · {s['monthSpan']} 个月 · "
                     f"{s['cityCount']} 座城市 · {s['storeCount']} 家门店\n")
    else:
        lines.append(f"{s['validOrders']} 笔有效订单 · 再积累 3 笔即可解锁画像\n")

    # 详细解读收进折叠区
    lines.append('<details><summary>展开详细解读</summary>\n')
    lines.append('| 维度 | 标签 | 解读 |')
    lines.append('|---|---|---|')
    lines.append(f"| 作息 | {persona['rhythm']['tag']} | {persona['rhythm']['desc']} |")
    lines.append(f"| 足迹 | {persona['geo']['tag']} | {persona['geo']['desc']} |")
    lines.append(f"| 消费 | {persona['spend']['tag']} | {persona['spend']['desc']} |")
    lines.append('')
    for name, desc in badges:
        lines.append(f'- **{name}** — {desc}')
    lines.append('')
    lines.append('</details>\n')

    lines.append('---')
    lines.append('*本画像由麦门情报站根据你的真实麦当劳订单数据生成，'
                 '仅为趣味性描述，不构成任何营养或健康建议。*')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='麦门人格画像生成器')
    ap.add_argument('--orders', help='order-list 返回的 JSON 文件')
    ap.add_argument('--points', help='query-my-account 返回的 JSON 字符串')
    ap.add_argument('--demo', action='store_true')
    args = ap.parse_args()

    if args.demo:
        sample = Path(__file__).parent.parent / 'data' / 'sample_orders.json'
        args.orders = str(sample)

    if not args.orders:
        ap.error('需要 --orders 参数，或使用 --demo')
        return 1

    raw = json.loads(Path(args.orders).read_text(encoding='utf-8'))
    orders = raw.get('data', {}).get('list', raw) if isinstance(raw, dict) else raw

    points = json.loads(args.points) if args.points else None

    persona = build_persona(orders, points)
    print(render_card(persona, points))
    return 0


if __name__ == '__main__':
    sys.exit(main())