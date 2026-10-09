#!/usr/bin/env python3
"""
麦麦情报站 - 开抢时刻解析器

从麦当劳 MCP 返回的原始数据中提取时间敏感情报，生成开抢倒计时简报。

数据来源：campaign-calendar / mall-points-products 等 MCP Tool 的返回内容。
本脚本不直接调用 MCP，而是解析 MCP 客户端传入的数据文件，
保证 Skill 可在任意 MCP Client 环境中复用。

用法:
    python3 intel_parser.py --calendar calendar.json [--mall mall.json] [--now 2026-10-09 15:30]
    python3 intel_parser.py --demo
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 开售时刻的常见表述模式
TIME_PATTERNS = [
    # 10月15日10:45开售 / 10月8日 10:30起
    (r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})\s*(?:起|开售|开抢|正式开售)', 'full'),
    # 10:45开售 / 每日14:00 准时开抢
    (r'(\d{1,2}):(\d{2})\s*(?:准时)?(?:开售|开抢|起售|开抢)', 'time_only'),
]

# 限量/抢购信号词 —— 命中即判定为高价值情报
URGENT_KEYWORDS = [
    '准时开抢', '先到先得', '每日限量', '每人限购', '限量', '抢先购',
    '仅限', '开售', '秒杀', '限量珍藏', '专属积分抢先购',
]

# 活动分组的英文/中文标签映射
CATEGORY_HINTS = [
    ('联名/周边', ['联名', '周边', '棒球帽', '徽章', '丝巾', 'T恤', '鞋']),
    ('新品上市', ['上新', '新', '回归', '爆款', '限定']),
    ('优惠套餐', ['元起', '两件套', '三件套', '四件套', '套餐', '早餐']),
    ('会员权益', ['麦金卡', '麦麦学社', '开学季', '卡', '会员']),
    ('亲子活动', ['派对', '读书会', '品鉴会', '体验营', '亲子', '儿童']),
]


def parse_calendar_intel(calendar_text: str, now: datetime):
    """从活动日历文本中提取开抢时刻。"""
    intel = []
    # 按活动块切分
    blocks = re.split(r'####\s*', calendar_text)
    for block in blocks:
        if not block.strip():
            continue
        title_match = re.search(r'\*\*活动标题\*\*：(.+)', block)
        if not title_match:
            continue
        title = title_match.group(1).strip()
        date_match = re.match(r'\s*(\d{4})年(\d{1,2})月(\d{1,2})日', block)
        day_offset = None
        if date_match:
            y, m, d = map(int, date_match.groups())
            day_offset = datetime(y, m, d)

        # 优先用「月日+时刻」的完整模式；仅当文本没写月日时才退回纯时刻模式。
        # 否则像「10月8日10:45开售」这种往期回顾会被纯时刻模式误判为未来的次日窗口。
        block_expired = False
        full_hits = list(re.finditer(TIME_PATTERNS[0][0], block))
        if full_hits:
            pattern, kind = TIME_PATTERNS[0]
            hits = full_hits
        else:
            pattern, kind = TIME_PATTERNS[1]
            hits = list(re.finditer(pattern, block))

        for hit in hits:
            if kind == 'full':
                mo, da, hh, mm = map(int, hit.groups())
                # 月份归属：若活动区块日期已知且月份不同，说明是跨月描述，
                # 以区块日期的年月为基准再调整月份，避免误推到下一年。
                base = day_offset or now
                year, month = base.year, mo
                if day_offset and day_offset.month > mo:
                    year = base.year - 1  # 区块在次月，回看的是上月活动
                cand = datetime(year, month, da, hh, mm)
                if cand < now:
                    # 显式写出的日期已过去，属往期回顾，不再作为情报输出
                    block_expired = True
                    break
            else:
                hh, mm = map(int, hit.groups())
                base = day_offset or now
                cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if cand < now - timedelta(hours=1):
                    cand = cand + timedelta(days=1)
            # 区块日期已知时，剔除明显早于该区块日期的误匹配
            if day_offset and cand < day_offset - timedelta(days=7):
                continue
            intel.append({
                'title': title,
                'start': cand,
                'context': hit.group(0),
                'urgent': any(k in block for k in URGENT_KEYWORDS),
            })
            break
        if block_expired:
            continue
    # 去重：同一活动保留最早的一个时刻
    seen, unique = set(), []
    for item in sorted(intel, key=lambda x: x['start']):
        if item['title'] in seen:
            continue
        seen.add(item['title'])
        unique.append(item)
    return unique


def parse_mall_intel(mall_data: list, now: datetime):
    """从积分商城商品中提取限量抢兑情报。"""
    intel = []
    for item in mall_data:
        status = item.get('status')
        point = int(item.get('point') or 0)
        selling = item.get('selling') or ''
        name = item.get('spuName') or ''
        # 仅保留上架状态、需积分、且有抢购信号的限量品
        if status != 2 or point <= 0:
            continue
        if not any(k in selling or k in name for k in URGENT_KEYWORDS):
            continue
        up = item.get('upTime') or ''
        try:
            start = datetime.strptime(up, '%Y-%m-%d %H:%M:%S') if up else None
        except ValueError:
            start = None
        intel.append({
            'name': name,
            'point': point,
            'price': item.get('price') or '0',
            'selling': selling,
            'start': start,
            'category': item.get('catName') or '',
        })
    return sorted(intel, key=lambda x: (x['start'] is None, x['start']))


def fmt_countdown(target: datetime, now: datetime) -> str:
    """格式化倒计时文案。"""
    delta = target - now
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 0:
        return '已开售'
    days, rem = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem, 60)
    if days > 0:
        return f'还有 {days} 天 {hours} 小时'
    if hours > 0:
        return f'还有 {hours} 小时 {minutes} 分钟'
    if minutes > 0:
        return f'还有 {minutes} 分钟'
    return '即将开始'


def classify(title: str) -> str:
    for label, keys in CATEGORY_HINTS:
        if any(k in title for k in keys):
            return label
    return '其他活动'


def build_report(calendar_text: str, mall_data: list, now: datetime) -> str:
    lines = []
    lines.append('# 麦麦情报站 · 开抢情报简报\n')
    lines.append(f'**情报基准时间**：{now.strftime("%Y-%m-%d %H:%M")}（北京时间）\n')

    cal = parse_calendar_intel(calendar_text, now)
    mall = parse_mall_intel(mall_data or [], now)

    upcoming = [c for c in cal if c['start'] and c['start'] >= now]
    urgent_soon = [m for m in mall if m['start'] and m['start'] >= now]

    # 最核心的一句话：下一个开抢窗口
    lines.append('## 下一个开抢窗口\n')
    if upcoming:
        nxt = upcoming[0]
        lines.append(f'> **{nxt["title"]}**\n')
        lines.append(f'> 开抢时刻：**{nxt["start"].strftime("%m月%d日 %H:%M")}**'
                     f'（{fmt_countdown(nxt["start"], now)}）\n')
        if len(upcoming) > 1:
            lines.append('\n后续窗口：\n')
            for c in upcoming[1:5]:
                lines.append(f'- {c["title"]} · '
                             f'{c["start"].strftime("%m月%d日 %H:%M")} · '
                             f'{fmt_countdown(c["start"], now)}')
    elif urgent_soon:
        nxt = urgent_soon[0]
        lines.append(f'> **{nxt["name"]}**（积分商城限量抢兑）\n')
        lines.append(f'> 开抢时刻：**{nxt["start"].strftime("%m月%d日 %H:%M")}**'
                     f'（{fmt_countdown(nxt["start"], now)}）\n')
    else:
        lines.append('> 近期无定时开抢活动，可关注积分商城的限量兑换与优惠券领取。\n')

    # 今日活动
    lines.append('\n## 活动分组\n')
    if upcoming:
        groups = {}
        for c in upcoming:
            groups.setdefault(classify(c['title']), []).append(c)
        for label, items in groups.items():
            lines.append(f'\n**{label}**\n')
            for it in items[:4]:
                flag = ' `抢购`' if it['urgent'] else ''
                lines.append(f'- {it["title"]} · '
                             f'{it["start"].strftime("%m月%d日 %H:%M")}{flag}')
    else:
        lines.append('- 暂无未来定时活动\n')

    # 限量抢兑
    if mall:
        lines.append('\n## 限量抢兑情报\n')
        lines.append('| 商品 | 所需积分 | 开抢时间 | 限购说明 |')
        lines.append('|---|---:|---|---|')
        for m in mall[:6]:
            st = m['start'].strftime('%m月%d日 %H:%M') if m['start'] else '按库存'
            selling = m['selling'].replace('\n', ' ')[:40]
            lines.append(f'| {m["name"]} | {m["point"]} | {st} | {selling} |')

    lines.append('\n---\n')
    lines.append('*本简报由麦麦情报站生成，数据来自麦当劳 MCP 实时接口。'
                 '活动时间、价格与库存以官方渠道实时结果为准。*')
    return '\n'.join(lines)


DEMO_CALENDAR = """#### 2026年10月15日

-   **活动标题**：麦当劳 X PEACEMINUSONE
    **活动内容介绍**：
周边第二弹——联名徽章即将发售
任意餐品消费加39.9元即可得
10月15日10:45开售
更有APP专享积分抢先购10:40开抢

#### 2026年10月10日

-   **活动标题**：预约麦金喜抽奖，积分霸王餐
    **活动内容介绍**：抽奖时间：10月10日 00:00开始
"""

DEMO_MALL = [
    {"spuName": "Nike Book 2 麦当劳特别联名款", "point": "8888", "price": "0",
     "selling": "独一无二，梦幻联动，每人限购一双，每日 14:00 准时开抢",
     "upTime": "2026-06-05 14:00:00", "status": 2, "catName": "鞋袜"},
    {"spuName": "罗技键鼠套装", "point": "6666", "price": "0",
     "selling": "积分兑大礼！每日限量 5 份，先到先得",
     "upTime": "2026-08-21 14:00:00", "status": 2, "catName": "玩具"},
]


def main():
    ap = argparse.ArgumentParser(description='麦麦情报站 · 开抢时刻解析器')
    ap.add_argument('--calendar', help='campaign-calendar 返回的文本文件')
    ap.add_argument('--mall', help='mall-points-products 返回的 JSON 文件')
    ap.add_argument('--now', help='当前时间，格式 "YYYY-MM-DD HH:MM"')
    ap.add_argument('--demo', action='store_true', help='使用内置示例数据运行')
    args = ap.parse_args()

    if args.demo:
        now = datetime.now()
        print(build_report(DEMO_CALENDAR, DEMO_MALL, now))
        return 0

    if not args.calendar:
        ap.error('需要 --calendar 参数，或使用 --demo')
        return 1

    calendar_text = Path(args.calendar).read_text(encoding='utf-8')
    mall_data = []
    if args.mall:
        raw = json.loads(Path(args.mall).read_text(encoding='utf-8'))
        mall_data = raw.get('data', raw) if isinstance(raw, dict) else raw

    now = datetime.strptime(args.now, '%Y-%m-%d %H:%M') if args.now else datetime.now()
    print(build_report(calendar_text, mall_data, now))
    return 0


if __name__ == '__main__':
    sys.exit(main())