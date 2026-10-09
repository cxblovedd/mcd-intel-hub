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
# 每个商品只归入首个命中类别（见 match_categories），避免重复计数
TASTE_RULES = {
    '甜品饮料': ['麦旋风', '圆筒', '新地', '派', '布丁', '圣代', '奶昔',
                '可乐', '雪碧', '咖啡', '奶铁', '茶', '豆浆', '橙'],
    '鸡腿风味': ['鸡腿堡', '板烧', '脆汁鸡', '麦辣鸡腿', '叫了个鸡', '辣味'],
    '深海鱼类': ['鳕鱼', '鱼排', '麦香鱼', '海苔'],
    '牛肉重口': ['巨无霸', '双层', '安格斯', '培根', '吉士汉堡', '芝士'],
    '麦满分早餐': ['麦满分', '猪柳蛋', '火腿扒', '蛋堡', '油条', '粥'],
    '小食组合': ['薯条', '麦乐鸡', '鸡翅', '鸡块', '脆薯饼', '小食', '玉米杯'],
    '酱香重口': ['韩式', '辣椒', '芝士棒', '蘸酱', '秘制', '酱'],
}

# 联名/周边关键词 —— 不参与口味评分，单列统计
MERCH_KEYWORDS = ['联名', '盲盒', '手办', '周边', '钥匙扣', 'T恤', '帽', '丝巾', '徽章']

# ── 8 型核心人格（命名统一为「XX + 人物角色」）───────────────
# key 需与 TASTE_RULES 的键对应
CORE_PERSONAS = {
    '甜品饮料': ('甜品鉴赏家', '别人吃饭，你品鉴'),
    '鸡腿风味': ('炸鸡教父', '酥脆鸡腿是你的金字招牌'),
    '深海鱼类': ('深海探索家', '麦门海底捞的常驻居民'),
    '牛肉重口': ('牛肉暴君', '双层芝士从不手软'),
    '麦满分早餐': ('早八掌门人', '早八人的命是麦满分给的'),
    '小食组合': ('小食收藏家', '单点小食才是快乐源泉'),
    '酱香重口': ('酱料炼金师', '你研究的是灵魂配方'),
}

# 联名人格：由联名购买数量独立判定，不参与口味评分竞争
MERCH_PERSONA = ('联名猎人', '你来麦当劳，八成不全是为了吃')

# ── 徽章定义 ─────────────────────────────────────────────────
# 每枚徽章有明确、可验证的解锁条件。
# 已解锁 → 展示名称 + 解锁依据；未解锁 → 展示剪影 + 条件提示（制造稀缺感与收集欲）
BADGE_DEFS = [
    {
        'key': '全勤王',
        'name': '麦门全勤王',
        'hint': '3 个不同日期都有有效订单',
    },
    {
        'key': '探险家',
        'name': '麦门探险家',
        'hint': '光顾 5 家不同门店',
    },
    {
        'key': '星期四',
        'name': '疯狂星期四卧底',
        'hint': '在周四下过单',
    },
    {
        'key': '全糖',
        'name': '全糖无悔',
        'hint': '单笔含2 份以上甜品单品',
    },
    {
        'key': '隐藏菜单',
        'name': '隐藏菜单大师',
        'hint': '订单中出现过 3 次餐品特制记录',
    },
    {
        'key': '联名收藏',
        'name': '联名收藏家',
        'hint': '买过 2 件以上联名周边',
    },
]

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


def match_categories(name):
    """判断餐品名命中哪些口味类别。

    每个商品只归入首个命中类别，避免「可乐中杯」同时计入甜品与饮品造成重复计数。
    联名类由 match_merch 单独判断，不走这套逻辑。
    """
    for cat, keywords in TASTE_RULES.items():
        if cat == '联名收藏':
            continue
        for kw in keywords:
            if kw in name:
                return [cat]
    return []


def match_merch(name):
    """判断是否为联名/周边商品。

    联名商品不参与口味评分 —— 买一次联名周边不足以定义人格。
    它的意义在于揭示「收藏倾向」，因此单列统计。
    """
    return any(kw in name for kw in MERCH_KEYWORDS)


def score_taste_weighted(orders):
    """加权评分：S = 0.5*F + 0.3*R + 0.2*P

    与「直接数次数」的关键区别：单次大量购买不会主导人格。

    F_c — 件数占比：该品类餐品件数 / 全部餐品件数
    R_c — 订单占比：包含该品类的订单数 / 有效订单数
    P_c — 复购稳定性：出现该品类的不同日期数 / 全部不同日期数

    联名类不参与口味评分（见 collect_categories），单列处理。
    """
    valid = [o for o in orders if o.get('orderStatus') == '订单已完成']
    if not valid:
        return {}, {}

    total_qty = 0
    category_qty = defaultdict(int)      # F 的分子
    category_orders = defaultdict(set)   # R 的分子
    category_days = defaultdict(set)     # P 的分子
    all_days = set()
    matched_detail = defaultdict(list)

    for order in valid:
        try:
            day = order.get('createTime', '')[:10]
            if day:
                all_days.add(day)
        except (TypeError, IndexError):
            day = ''

        order_cats = set()
        for name, qty in flatten_items([order]):
            total_qty += qty
            for cat in match_categories(name):
                category_qty[cat] += qty
                category_orders[cat].add(order.get('orderId', id(order)))
                if day:
                    category_days[cat].add(day)
                matched_detail[cat].append(name)
                order_cats.add(cat)

    if total_qty == 0 or not all_days:
        return {}, {}

    n_orders = len(valid)
    scores = {}
    for cat in category_qty:
        f = category_qty[cat] / total_qty
        r = len(category_orders[cat]) / n_orders
        p = len(category_days[cat]) / len(all_days)
        scores[cat] = round(0.5 * f + 0.3 * r + 0.2 * p, 4)

    return scores, dict(matched_detail)


def score_rhythm(orders):
    """分析下单时间节律：晨光捕手 / 午间充电站 / 黄昏漫游者 / 深夜觅食者。"""
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
            slots['晨光捕手'] += 1
        elif 10 <= h < 14:
            slots['午间充电站'] += 1
        elif 14 <= h < 18:
            slots['黄昏漫游者'] += 1
        else:
            slots['深夜觅食者'] += 1
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



def evaluate_badges(valid, stores, merch_count):
    """逐条判定徽章是否解锁。

    返回 [{key,name,hint,unlocked,proof}]，未解锁的保留在列表中，
    由渲染层输出剪影与解锁提示。
    """
    days = set()
    thursday_count = 0
    sweet_order_count = 0
    customize_count = 0

    SWEET_KEYWORDS = ('麦旋风', '圆筒', '新地', '派', '布丁', '圣代')
    CUSTOMIZE_KEYWORDS = ('特制', '特调', '定制', '不加')

    for order in valid:
        try:
            ts = datetime.strptime(order.get('createTime', ''), '%Y-%m-%d %H:%M:%S')
            days.add(ts.strftime('%Y-%m-%d'))
            if ts.weekday() == 3:
                thursday_count += 1
        except (ValueError, TypeError):
            pass

        # 单笔甜品份数（需展开套餐子项）
        sweet_qty = sum(qty for name, qty in flatten_items([order])
                        if any(k in name for k in SWEET_KEYWORDS))
        if sweet_qty >= 2:
            sweet_order_count += 1

        # 餐品特制记录：comboItemList 的 itemName 含定制类关键词
        for prod in order.get('orderProductList') or []:
            for sub in prod.get('comboItemList') or []:
                sname = sub.get('name') or ''
                if any(k in sname for k in CUSTOMIZE_KEYWORDS):
                    customize_count += 1

    proofs = {
        '全勤王': f'{len(days)} 个不同日期有有效订单' if len(days) >= 3 else None,
        '探险家': f'打卡 {len(stores)} 家门店' if len(stores) >= 5 else None,
        '星期四': f'{thursday_count} 笔周四订单' if thursday_count >= 1 else None,
        '全糖': f'{sweet_order_count} 笔含 2 份以上甜品'
                if sweet_order_count >= 1 else None,
        '隐藏菜单': f'{customize_count} 条特制记录' if customize_count >= 3 else None,
        '联名收藏': f'联名周边 {merch_count} 件' if merch_count >= 2 else None,
    }

    result = []
    for badge in BADGE_DEFS:
        proof = proofs.get(badge['key'])
        result.append({
            'key': badge['key'],
            'name': badge['name'],
            'hint': badge['hint'],
            'unlocked': proof is not None,
            'proof': proof or '',
        })
    return result


def build_persona(orders, points=None):
    """核心：根据订单数据推导麦门人格。

    判定优先级：
    1. 联名人格 —— 联名购买独立判定，不与口味竞争
    2. 核心人格 —— 加权评分 S=0.5F+0.3R+0.2P，取最高分
    3. 副人格 —— 与主人格得分接近时并列展示
    4. 置信度 —— 按有效订单数分级
    """
    valid = [o for o in orders if o.get('orderStatus') == '订单已完成']
    items = flatten_items(valid)

    # 加权评分（联名类不参与）
    taste_scores, taste_detail = score_taste_weighted(valid)
    slots, hours = score_rhythm(valid)
    stores, cities = analyze_geo(valid)

    # 联名商品单独统计
    merch_count = sum(qty for name, qty in items if match_merch(name))

    amounts = []
    zero_orders = 0
    for o in valid:
        try:
            amt = float(o.get('realTotalAmount') or 0)
            if amt > 0:
                amounts.append(amt)
            else:
                zero_orders += 1
        except (ValueError, TypeError):
            continue

    month_span = _month_span(valid)
    accumulative_point = 0.0
    if points:
        try:
            accumulative_point = float(points.get('accumulativePoint') or 0)
        except (ValueError, TypeError):
            accumulative_point = 0.0

    n_valid = len(valid)

    # ── 置信度分级 ──
    if n_valid <= 2:
        confidence = '探索中'
        confidence_desc = f'仅 {n_valid} 笔有效订单，再积累 {3 - n_valid} 笔可解锁人格'
    elif n_valid <= 5:
        confidence = '初步画像'
        confidence_desc = f'{n_valid} 笔有效订单，样本较少，人格可能随消费变化'
    else:
        confidence = '正式画像'
        confidence_desc = f'基于最近 {n_valid} 笔有效订单'

    # ── 人格判定：联名优先，其次加权分最高者 ──
    ranked = sorted(taste_scores.items(), key=lambda x: x[1], reverse=True)

    if merch_count >= 2 and merch_count >= 0.25 * sum(q for _, q in items):
        persona, tagline_used = MERCH_PERSONA
        desc_text = f'联名周边 {merch_count} 件，占比超过四分之一'
        sub_persona = None
    elif not ranked or n_valid < 3:
        persona = '麦门新客'
        tagline_used = '你的麦门故事刚刚开始'
        desc_text = (f'目前只有 {n_valid} 笔有效订单，'
                     f'再积累 {max(0, 3 - n_valid)} 笔就能解锁人格')
        sub_persona = None
    else:
        primary, top_score = ranked[0]
        persona, tagline_used = CORE_PERSONAS.get(
            primary, ('麦门常客', '均衡而稳定的选择'))
        top_qty = sum(taste_detail.get(primary, []).__len__() for _ in [0]) or len(
            taste_detail.get(primary, []))
        desc_text = f'{primary} 主导 · 加权得分 {top_score:.3f}'

        # 副人格：分差小于 25% 时并列展示，比强行归类更诚实
        sub_persona = None
        if len(ranked) > 1:
            second_cat, second_score = ranked[1]
            if top_score > 0 and second_score / top_score >= 0.75:
                sub_name, sub_tagline = CORE_PERSONAS.get(
                    second_cat, ('麦门常客', ''))
                sub_persona = {
                    'name': sub_name,
                    'tagline': sub_tagline,
                    'ratio': round(second_score / top_score * 100),
                }

    # ── 徽章判定（可解锁条件明确，未达成显示剪影）──
    badges = evaluate_badges(valid, stores, merch_count)

    # ── 维度二：作息 ──
    if slots:
        top_slot = slots.most_common(1)[0][0]
        slot_desc = {
            '晨光捕手': '清晨的第一口，是一天的正确打开方式',
            '午间充电站': '午间补给，稳定输出型选手',
            '黄昏漫游者': '下班路上的一小段快乐',
            '深夜觅食者': '夜里那盏灯，是为你留的',
        }
        rhythm = slot_desc.get(top_slot, '')
    else:
        top_slot = '数据不足'
        rhythm = '时间数据不足，多点几单试试'

    # ── 维度三：足迹（含外送/到店判定）──
    store_count = len(stores)
    city_count = len(cities)
    delivery_count = sum(1 for o in valid if str(o.get('beType')) == '2')
    delivery_ratio = delivery_count / n_valid if n_valid else 0

    if delivery_ratio >= 0.6:
        geo_tag = '外送宅家派'
        geo_desc = f'{delivery_count}/{n_valid} 笔是外送， doorstep 直达'
    elif city_count >= 3:
        geo_tag = '跨城旅行家'
        geo_desc = f'足迹遍布 {city_count} 座城市，{store_count} 家门店'
    elif store_count >= 4:
        geo_tag = '城市漫游者'
        geo_desc = f'常驻 {store_count} 家门店，认准那几个熟悉的味道'
    elif store_count >= 1:
        geo_tag = '据点守护者'
        geo_desc = f'你的麦门据点是 {store_count} 家门店'
    else:
        geo_tag = '数据不足'
        geo_desc = '门店数据不足'

    # ── 维度四：消费习惯（按可获取字段判定）──
    paid_orders = len(amounts)
    total_items = sum(q for _, q in items)
    distinct_items = len({name for name, _ in items})
    diversity = distinct_items / total_items if total_items else 0
    repeat_ratio = _repeat_ratio(items)

    if n_valid == 0:
        spend_tag = '数据不足'
        spend_desc = '暂无有效订单'
    elif zero_orders == n_valid:
        spend_tag = '权益型消费者'
        spend_desc = '全部为积分兑换或优惠订单，用权益把成本压得很低'
    elif repeat_ratio >= 0.5 and len(ranked) <= 2:
        spend_tag = '经典复刻派'
        spend_desc = f'高频复刻同一批餐品，{repeat_ratio*100:.0f}% 的点单都是老朋友'
    elif any(o.get('orderProductList') and
             any(p.get('comboItemList') for p in o['orderProductList'])
             for o in valid):
        spend_tag = '套餐规划师'
        spend_desc = '习惯点成套组合，一单解决一顿'
    elif diversity >= 0.7:
        spend_tag = '口味探索者'
        spend_desc = f'{distinct_items} 种不同餐品，菜单翻得很勤'
    else:
        spend_tag = '零点捕手'
        spend_desc = f'{zero_orders}/{n_valid} 笔零元订单，优惠用得勤'

    return {
        'persona': persona,
        'tagline': tagline_used,
        'desc': desc_text,
        'subPersona': sub_persona,
        'confidence': confidence,
        'confidenceDesc': confidence_desc,
        'badges': badges,
        'rhythm': {'tag': top_slot, 'desc': rhythm},
        'geo': {'tag': geo_tag, 'desc': geo_desc, 'stores': store_count,
                'cities': city_count, 'deliveryRatio': round(delivery_ratio, 2)},
        'spend': {'tag': spend_tag, 'desc': spend_desc, 'avg': round(sum(amounts) / len(amounts), 1) if amounts else 0,
                  'paidOrders': paid_orders, 'zeroOrders': zero_orders,
                  'repeatRatio': round(repeat_ratio, 2)},
        'taste': dict(taste_scores),
        'tasteDetail': {k: len(v) for k, v in taste_detail.items()},
        'merchCount': merch_count,
        'stats': {
            'totalOrders': len(orders),
            'validOrders': n_valid,
            'itemCount': total_items,
            'distinctItems': distinct_items,
            'storeCount': store_count,
            'cityCount': city_count,
            'monthSpan': month_span,
            'accumulativePoint': accumulative_point,
        },
    }


def _repeat_ratio(items):
    """复刻率：出现超过一次的餐品件数占总件数比例。"""
    if not items:
        return 0
    counter = Counter(name for name, _ in items)
    total = sum(q for _, q in items)
    repeated = sum(q for name, q in items if counter[name] > 1)
    return repeated / total if total else 0


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
    detail = persona.get('tasteDetail') or {}
    max_score = max(top[0][1], 0.0001) if top else 1

    lines = []
    lines.append('# 你的麦门人格\n')
    lines.append(f"置信度：**{persona.get('confidence', '—')}**"
                 f"（{persona.get('confidenceDesc', '')}）\n")
    lines.append('## ' + persona['persona'] + '\n')
    lines.append('> ' + persona['tagline'] + '\n')

    # 副人格：得分接近时并列展示
    sub = persona.get('subPersona')
    if sub:
        lines.append(f"**隐藏副人格**：{sub['name']}"
                     f"（与主人格接近 {sub['ratio']}%）\n")

    # 徽章：已解锁点亮，未解锁显示剪影与解锁条件
    badges = persona.get('badges') or []
    unlocked = [b for b in badges if b['unlocked']]
    locked = [b for b in badges if not b['unlocked']]
    if unlocked:
        lines.append('**已解锁徽章**\n')
        for b in unlocked:
            lines.append(f"- {b['name']} — {b['proof']}")
        lines.append('')
    if locked:
        lines.append('**未解锁徽章**\n')
        for b in locked:
            lines.append(f"- ▢▢▢ — {b['hint']}")
        lines.append('')

    # 口味权重 —— 人格的主要依据
    if top:
        lines.append('### 你点的都是什么\n')
        for tag, score in top:
            pct = score / max_score * 100
            bar = '█' * max(1, int(pct / 10))
            cnt = detail.get(tag, 0)
            lines.append(f'{tag} {bar} {score:.3f}')
        lines.append('')

    # 三个行为标签压成一行
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
    lines.append('加权公式：S = 0.5×件数占比 + 0.3×订单占比 + 0.2×复购稳定性')
    lines.append('')
    lines.append('</details>\n')

    lines.append('---')
    lines.append('*本画像由麦门人格生成，根据你的真实麦当劳订单数据推导，'
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