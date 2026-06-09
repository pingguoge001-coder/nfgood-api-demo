"""产品数据同步：小程序 → 飞书多维表格
同步库存、价格、状态，滚动快照，汇总近30天订单销量

用法：
  python sync_products.py                # 全量同步在售商品
  python sync_products.py --id xxxx      # 同步指定商品ID
  python sync_products.py --no-orders    # 不拉订单（更快）
"""
import hashlib, json, time, random, string, os, sys, requests, urllib3
from datetime import datetime, timedelta

urllib3.disable_warnings()

# ===== 小程序配置（填你自己的，开通花城农夫开放平台后获得）=====
PARTNER_ID = '<你的_partnerId>'
SECRET = '<你的_secret>'

# ===== 飞书配置（填你自己的飞书自建应用凭证；只有用到飞书同步才需要）=====
FEISHU_APP_ID = '<你的_飞书app_id>'
FEISHU_APP_SECRET = '<你的_飞书app_secret>'
FEISHU_BASE_TOKEN = '<你的_多维表格base_token>'
FEISHU_TABLE_ID = '<你的_表格id>'
FEISHU_MIN_INTERVAL = 0.5

feishu_token = None
feishu_token_expire = 0
last_feishu_call = 0

# ===== 小程序API =====
def call_api(method, body):
    nonce_str = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    timestamp = int(time.time())
    body_json = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
    sign_str = 'method=%s&nonceStr=%s&partnerId=%s&timestamp=%s&%s&%s' % (
        method, nonce_str, PARTNER_ID, timestamp, body_json, SECRET)
    sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()
    headers = {
        'Content-Type': 'application/json',
        'partnerId': PARTNER_ID, 'nonceStr': nonce_str,
        'timestamp': str(timestamp), 'sign': sign
    }
    resp = requests.post('https://open.nfgood.com/api/v1/%s' % method,
                         data=body_json, headers=headers, timeout=120, verify=False)
    return resp.json()

# ===== 拉取在售商品 =====
def fetch_goods(target_id=None):
    """拉取在售商品列表，或搜索指定ID"""
    print('拉取在售商品...', flush=True)
    all_goods = []
    skip = 0
    while True:
        data = call_api('listGoods', {'under': 'SELL', 'source': 'BIND', 'key': '', 'skip': skip, 'limit': 500})
        goods = data.get('data', {}).get('listGoods', [])
        all_goods.extend(goods)
        if len(goods) < 500:
            break
        skip += 500
        time.sleep(0.3)
    print('共 %d 个在售商品' % len(all_goods), flush=True)

    if target_id:
        filtered = [g for g in all_goods if g.get('id') == target_id]
        if not filtered:
            print('未找到商品ID: %s' % target_id)
            return []
        return filtered
    return all_goods

# ===== 拉取近30天订单，按商品汇总销量 =====
def fetch_order_summary():
    """返回 {商品ID: 销量} 字典"""
    print('拉取近30天订单...', flush=True)
    end = datetime.now()
    start = end - timedelta(days=30)
    summary = {}

    # 按月拉取避免超时
    current = start
    while current < end:
        month_end = min(current + timedelta(days=30), end)
        begin_str = current.strftime('%Y-%m-%d')
        end_str = month_end.strftime('%Y-%m-%d')

        skip = 0
        while True:
            data = call_api('listOrders', {
                'beginDate': begin_str, 'endDate': end_str,
                'supplyType': 'ALL', 'skip': skip, 'limit': 500
            })
            orders = data.get('data', {}).get('listOrders', [])
            for order in orders:
                for item in (order.get('goodsInfo') or []):
                    gid = item.get('id', '')
                    num = item.get('num', 0)
                    if gid:
                        summary[gid] = summary.get(gid, 0) + num
            if len(orders) < 500:
                break
            skip += 500
            time.sleep(0.3)

        current = month_end
        time.sleep(0.3)

    print('订单汇总: %d 个商品有销量' % len(summary), flush=True)
    return summary

# ===== 飞书API =====
def get_feishu_token():
    global feishu_token, feishu_token_expire
    if feishu_token and time.time() < feishu_token_expire:
        return feishu_token
    resp = requests.post(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        json={'app_id': FEISHU_APP_ID, 'app_secret': FEISHU_APP_SECRET},
        timeout=15, verify=False)
    data = resp.json()
    feishu_token = data['tenant_access_token']
    feishu_token_expire = time.time() + data.get('expire', 7200) - 60
    return feishu_token

def feishu_wait():
    global last_feishu_call
    wait = FEISHU_MIN_INTERVAL - (time.time() - last_feishu_call)
    if wait > 0:
        time.sleep(wait)
    last_feishu_call = time.time()

def feishu_search(goods_id):
    """按商品ID搜索飞书记录，返回 (record_id, fields) 或 (None, None)"""
    feishu_wait()
    token = get_feishu_token()
    resp = requests.post(
        'https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records/search' % (FEISHU_BASE_TOKEN, FEISHU_TABLE_ID),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        json={
            'page_size': 1,
            'filter': {'conjunction': 'and', 'conditions': [
                {'field_name': '商品ID', 'operator': 'is', 'value': [goods_id]}
            ]},
            'field_names': ['商品ID', '当前库存数量', '前1次库存', '前1次同步时间',
                            '前2次库存', '前2次同步时间', '前3次库存', '前3次同步时间',
                            '最后同步时间']
        },
        timeout=15, verify=False)
    result = resp.json()
    if result.get('code') == 99991400:
        time.sleep(5)
        return None, None
    items = result.get('data', {}).get('items', [])
    if not items:
        return None, None
    return items[0]['record_id'], items[0].get('fields', {})

def feishu_update(record_id, fields):
    """更新飞书记录"""
    feishu_wait()
    token = get_feishu_token()
    resp = requests.put(
        'https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records/%s' % (FEISHU_BASE_TOKEN, FEISHU_TABLE_ID, record_id),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        json={'fields': fields},
        timeout=15, verify=False)
    result = resp.json()
    if result.get('code') == 99991400:
        time.sleep(5)
        return False
    return result.get('code') == 0

def feishu_create(stock, price, cost, now_ms):
    """在飞书新建一条空记录（先写数字字段），返回 record_id 或 None"""
    feishu_wait()
    token = get_feishu_token()
    resp = requests.post(
        'https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records' % (FEISHU_BASE_TOKEN, FEISHU_TABLE_ID),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        json={'fields': {
            '当前库存数量': stock,
            '零售价': price,
            '成本价': cost,
            '最后同步时间': now_ms,
        }},
        timeout=15, verify=False)
    result = resp.json()
    if result.get('code') != 0:
        return None
    return result.get('data', {}).get('record', {}).get('record_id')

def extract_detail_text(g):
    parts = []
    for item in (g.get('listContent') or []):
        if item.get('type') == 'CONTENT':
            text = item.get('content', '').strip()
            if text:
                parts.append(text)
    return '\n\n'.join(parts)

def format_specs(specs):
    lines = []
    for s in specs:
        name = s.get('name', '')
        price = s.get('price', 0) / 100
        cost = s.get('supplyFee', 0) / 100
        stock = s.get('limitCount', 0)
        lines.append('%s | ¥%.0f/%.0f | 库存%d件' % (name, price, cost, stock))
    return '\n'.join(lines)

# ===== 同步单个商品 =====
def sync_one(g, order_summary):
    """同步一个商品到飞书，返回 (成功, 信息)"""
    goods_id = g.get('id', '')
    name = g.get('name', '')
    specs = g.get('specs', [])

    # 计算新数据
    new_stock = sum(s.get('limitCount', 0) for s in specs)
    new_price = specs[0].get('price', 0) / 100 if specs else 0
    new_cost = specs[0].get('supplyFee', 0) / 100 if specs else 0
    new_status = '已下架' if g.get('isUnder') else '已上架'
    sales_30d = order_summary.get(goods_id, 0)
    detail_text = extract_detail_text(g)
    specs_text = format_specs(specs)

    # 搜索飞书记录
    record_id, old_fields = feishu_search(goods_id)

    now_ms = int(time.time() * 1000)

    if not record_id:
        # 新商品：建行
        record_id = feishu_create(new_stock, new_price, new_cost, now_ms)
        if not record_id:
            return False, '新建失败'
        feishu_update(record_id, {'商品ID': goods_id, '产品名称': name, '商品详情内容': detail_text, '产品规格数据': specs_text})
        feishu_update(record_id, {'产品状态': new_status})
        if sales_30d > 0:
            feishu_update(record_id, {'累计销量-订单': sales_30d})
        return True, '[新建] 库存%d 价%.0f/%.0f 销量%d' % (new_stock, new_price, new_cost, sales_30d)

    # 读取旧值（用于滚动快照）
    old_stock = old_fields.get('当前库存数量', 0) or 0
    old_sync_time = old_fields.get('最后同步时间', 0) or 0
    old_p1_stock = old_fields.get('前1次库存', 0) or 0
    old_p1_time = old_fields.get('前1次同步时间', 0) or 0
    old_p2_stock = old_fields.get('前2次库存', 0) or 0
    old_p2_time = old_fields.get('前2次同步时间', 0) or 0

    # 构建更新字段
    update = {
        # 滚动快照
        '前3次库存': old_p2_stock,
        '前3次同步时间': old_p2_time,
        '前2次库存': old_p1_stock,
        '前2次同步时间': old_p1_time,
        '前1次库存': old_stock,
        '前1次同步时间': old_sync_time if old_sync_time else now_ms,
        # 新数据
        '当前库存数量': new_stock,
        '零售价': new_price,
        '成本价': new_cost,
        '最后同步时间': now_ms,
    }

    # 先更新数字字段
    ok1 = feishu_update(record_id, update)
    if not ok1:
        return False, '数字字段更新失败'

    # 再更新文本和单选字段（分开避免类型冲突）
    feishu_update(record_id, {'产品名称': name, '商品详情内容': detail_text, '产品规格数据': specs_text})
    feishu_update(record_id, {'产品状态': new_status})

    # 更新销量
    if sales_30d > 0:
        feishu_update(record_id, {'累计销量-订单': sales_30d})

    return True, '库存%d 价%.0f/%.0f 销量%d' % (new_stock, new_price, new_cost, sales_30d)

# ===== 主流程 =====
if __name__ == '__main__':
    # 解析参数
    target_id = None
    no_orders = '--no-orders' in sys.argv
    limit_count = 0
    if '--id' in sys.argv:
        idx = sys.argv.index('--id')
        if idx + 1 < len(sys.argv):
            target_id = sys.argv[idx + 1]
    if '--limit' in sys.argv:
        idx = sys.argv.index('--limit')
        if idx + 1 < len(sys.argv):
            limit_count = int(sys.argv[idx + 1])

    print('===== 产品数据同步 =====', flush=True)
    print('时间: %s' % time.strftime('%Y-%m-%d %H:%M:%S'), flush=True)

    # 拉取商品
    goods = fetch_goods(target_id)
    if not goods:
        print('没有要同步的商品')
        sys.exit(0)

    # 限制数量
    if limit_count and len(goods) > limit_count:
        goods = goods[:limit_count]
        print('限制前 %d 个商品' % limit_count, flush=True)

    # 拉取订单销量
    order_summary = {}
    if not no_orders:
        try:
            order_summary = fetch_order_summary()
        except Exception as e:
            print('订单拉取失败，跳过销量同步: %s' % str(e)[:50])

    # 逐个同步
    success = 0
    fail = 0
    not_found = 0
    start_time = time.time()

    for i, g in enumerate(goods):
        name = g.get('name', '')[:25]
        gid = g.get('id', '')

        try:
            ok, info = sync_one(g, order_summary)
            if ok:
                success += 1
                print('[%d/%d] %s  %s' % (i+1, len(goods), name, info), flush=True)
            else:
                if '未找到' in info:
                    not_found += 1
                else:
                    fail += 1
                print('[%d/%d] %s  %s' % (i+1, len(goods), name, info), flush=True)
        except Exception as e:
            fail += 1
            print('[%d/%d] %s  错误: %s' % (i+1, len(goods), name, str(e)[:50]), flush=True)

        # 每50个汇报
        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            print('--- 进度 %d/%d | 成功%d 失败%d 未找到%d | %.0f秒 ---' % (
                i+1, len(goods), success, fail, not_found, elapsed), flush=True)

    elapsed = time.time() - start_time
    print()
    print('===== 同步完成 =====')
    print('商品: %d | 成功: %d | 失败: %d | 飞书未找到: %d' % (len(goods), success, fail, not_found))
    print('耗时: %.1f秒 (%.1f分钟)' % (elapsed, elapsed / 60))
