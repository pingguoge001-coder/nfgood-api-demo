# -*- coding: utf-8 -*-
"""
花城农夫小程序 —— 上传商品到草稿箱工具（2026-06-11 实测打通）

两种用法：
  1) 克隆已有商品（最省事，图直接复用，不碰图床）：
     python upload_goods.py --clone 源商品ID [--name 新名]

  2) 新建商品 + 本地图（本地图自动走 litterbox→微购→szwego 中转拿URL）：
     python upload_goods.py --name "商品名" --image 图1.jpg --image 图2.jpg \
        --price 38 --supply 23 --spec "5斤装" --detail "一段卖点文案"

==== 实测结论（别踩坑）====
  - 花城农夫接口要图片URL，且只认自己(img.nfgood.com)和微购(xcimg.szwego.com)域名的图；
    litterbox 等外部野图床的图传上去【不显示】（花城农夫前端加载不出来）。
  - 本地图没有花城农夫图床URL时，唯一通路：本地图→litterbox临时中转→微购save(微购异步抓取转存)
    →拿到 szwego 永久URL→再喂花城农夫。微购转存约 2-3 分钟，脚本会轮询等待。
  - detailByItemIds 接口参数是 goodsId（不是itemIds/goodsIds），按ID查实时；
    查列表(query/list)有几分钟索引延迟，别用列表查刚save的。
  - 分类(types)接口设不上，要后台手动；草稿箱商品【不能】接口发布上架，要后台手动点。
  - ⚠️ 每张本地图中转，都会在微购相册留一条"图床中转"标签的条目，【不能删】——
    花城农夫的图就挂在这条上面，删了图会失效。

==== 用之前：填你自己的凭证 ====
  - 花城农夫 partnerId/secret：开通花城农夫开放平台后获得
  - 微购 AUTH_KEY/AUTH_SECRET：开通微购（微商相册）开放平台后获得，仅本地图中转才需要
"""
import argparse, hashlib, json, time, random, string, sys, ssl, base64
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

# ===== 花城农夫凭证（填你自己的）=====
NF_PARTNER_ID = '<你的_花城农夫partnerId>'
NF_SECRET = '<你的_花城农夫secret>'
NF_BASE = 'https://open.nfgood.com/api/v1/'

# ===== 微购相册凭证（仅图片中转用，填你自己的）=====
WG_AUTH_KEY = '<你的_微购AUTH_KEY>'
WG_AUTH_SECRET = '<你的_微购AUTH_SECRET>'
WG_BASE = 'https://www.szwego.com'

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


# ---------- 花城农夫接口（MD5签名）----------
def nf_call(method, body):
    nonce = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    ts = int(time.time())
    bj = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
    sign_str = 'method=%s&nonceStr=%s&partnerId=%s&timestamp=%s&%s&%s' % (
        method, nonce, NF_PARTNER_ID, ts, bj, NF_SECRET)
    sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()
    headers = {'Content-Type': 'application/json', 'partnerId': NF_PARTNER_ID,
               'nonceStr': nonce, 'timestamp': str(ts), 'sign': sign}
    req = urllib.request.Request(NF_BASE + method, data=bj.encode('utf-8'),
                                 headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=60, context=_ctx) as r:
        return json.loads(r.read().decode('utf-8'))


# ---------- 图片中转：本地图 → litterbox → 微购转存 → szwego永久URL ----------
def _litterbox(path):
    """本地图传 litterbox 临时图床（72小时），返回公网URL"""
    with open(path, 'rb') as f:
        img = f.read()
    fn = Path(path).name
    b = '----FormBoundary7MA4YWxkTrZu0gW'
    body = (
        f'--{b}\r\nContent-Disposition: form-data; name="reqtype"\r\n\r\nfileupload\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="time"\r\n\r\n72h\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="fileToUpload"; filename="{fn}"\r\n'
        f'Content-Type: image/jpeg\r\n\r\n'
    ).encode() + img + f'\r\n--{b}--\r\n'.encode()
    req = urllib.request.Request(
        'https://litterbox.catbox.moe/resources/internals/api.php',
        data=body, headers={'Content-Type': f'multipart/form-data; boundary={b}'},
        method='POST')
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode().strip()


def _wg_auth():
    return 'Basic ' + base64.b64encode(f'{WG_AUTH_KEY}:{WG_AUTH_SECRET}'.encode()).decode()


def _wg_save(title, img_urls):
    """把图URL存进微购（微购会抓取转存成自己的szwego URL），返回微购商品ID"""
    payload = {'title': title, 'themeType': 0, 'mainImages': img_urls,
               'tags': [{'tagName': '图床中转', 'tagId': 0}]}
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(WG_BASE + '/open/api/v3/commodity/operate/save',
                                 data=data, method='POST',
                                 headers={'Authorization': _wg_auth(),
                                          'Content-Type': 'application/json; charset=utf-8'})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read())
    if res.get('errcode') != 0:
        raise RuntimeError('微购save失败: %s' % res.get('errmsg'))
    return (res.get('result') or {}).get('goodsId') or res.get('goodsId', '')


def _wg_get_image(goods_id):
    """按ID查微购商品详情，返回mainImages（转存完成后是szwego永久URL）。
    实测：detailByItemIds 接口参数是 goodsId（不是itemIds/goodsIds），按ID查是实时的。"""
    data = json.dumps({'goodsId': goods_id}).encode('utf-8')
    req = urllib.request.Request(WG_BASE + '/open/api/v3/commodity/query/detailByItemIds',
                                 data=data, method='POST',
                                 headers={'Authorization': _wg_auth(),
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        res = json.loads(r.read().decode('utf-8'))
    items = res.get('result') or []
    return items[0].get('mainImages', '') if items else ''


def local_image_to_nfurl(path):
    """本地图 → 花城农夫能用的 szwego 永久URL"""
    print('  [中转] %s' % Path(path).name, flush=True)
    litter = _litterbox(path)
    print('    litterbox: %s' % litter, flush=True)
    title = '图床中转_%d_%s' % (int(time.time()), Path(path).stem)
    gid = _wg_save(title, [litter])
    print('    微购条目: %s（等微购把图转存成永久URL，可能要几分钟…）' % gid, flush=True)
    # 微购 save 后存的是litterbox原URL，后台异步抓取转存成szwego永久URL，较慢。
    # 用 detailByItemIds 按ID轮询，每10秒一次，最多等约5分钟，直到URL变 szwego。
    szwego = ''
    for i in range(30):
        time.sleep(10)
        cur = _wg_get_image(gid)
        if cur and 'szwego' in cur:
            szwego = cur
            break
        print('    …第%d次查，微购还没转存完' % (i + 1), flush=True)
    if not szwego:
        raise RuntimeError('微购转存超时（>5分钟）。图已存微购 goodsId=%s，可稍后重试' % gid)
    print('    szwego: %s' % szwego, flush=True)
    return szwego


# ---------- 主功能 ----------
def clone_goods(src_id, new_name=None):
    """克隆已有商品成新草稿（图直接复用，最省事）"""
    g = nf_call('goodsInfo', {'goodsId': src_id}).get('data', {}).get('goodsInfo')
    if not g:
        print('源商品查不到: %s' % src_id)
        sys.exit(1)
    specs = [{'name': s.get('name', ''), 'retailFee': s.get('price', 0),
              'supplyFee': s.get('supplyFee', 0)} for s in (g.get('specs') or [])]
    new = {'name': new_name or g.get('name', ''),
           'logo': g.get('logo', ''),
           'contents': g.get('listContent') or [],
           'specs': specs,
           'types': g.get('types', [])}
    return nf_call('syncGoods', new)


def new_goods(name, images, price, supply, spec_name, detail):
    """新建商品，本地图自动中转拿URL"""
    contents = []
    if detail:
        contents.append({'type': 'CONTENT', 'content': detail, 'url': ''})
    logo = ''
    for img in (images or []):
        url = local_image_to_nfurl(img)
        if not logo:
            logo = url  # 第一张图当主图
        contents.append({'type': 'IMAGE', 'content': '', 'url': url})
    new = {'name': name, 'logo': logo, 'contents': contents,
           'specs': [{'name': spec_name or '默认规格',
                      'retailFee': int(price * 100),   # 元转分
                      'supplyFee': int(supply * 100)}]}
    return nf_call('syncGoods', new)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='花城农夫上传商品到草稿箱')
    p.add_argument('--clone', help='克隆模式：源商品ID')
    p.add_argument('--name', help='商品名（克隆时可选覆盖；新建时必填）')
    p.add_argument('--image', action='append', default=[],
                   help='本地图片路径，可多张（新建模式，自动中转）')
    p.add_argument('--price', type=float, default=0, help='零售价（元）')
    p.add_argument('--supply', type=float, default=0, help='供货价（元）')
    p.add_argument('--spec', help='规格名，如"5斤装"')
    p.add_argument('--detail', help='详情文案')
    args = p.parse_args()

    if args.clone:
        r = clone_goods(args.clone, args.name)
    elif args.name:
        r = new_goods(args.name, args.image, args.price, args.supply, args.spec, args.detail)
    else:
        p.print_help()
        sys.exit(1)

    print('===== syncGoods 返回 =====')
    print(json.dumps(r, ensure_ascii=False, indent=2))
    gid = (r.get('data') or {}).get('syncGoods', '')
    if gid:
        print('\n✅ 草稿创建成功 商品ID=%s' % gid)
        print('⚠️ 这是草稿，去花城农夫后台草稿箱手动发布上架；分类需后台手动设')
    else:
        print('\n❌ 失败:', r.get('errors'))
