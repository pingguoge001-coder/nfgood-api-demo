# 花城农夫（nfgood）小程序开放接口 · 调用示例

> 花城农夫小程序开放平台接口的调用方法 + 一个真实可跑的 Python 同步脚本示例。
> 给一起学习的小伙伴参考——**所有密钥已脱敏，用之前先填自己的**。

## 这是什么

- `花城小程序接口文档.md` —— nfgood 开放平台 14 个接口说明（地址、签名规则、参数）
- `sync_products.py` —— 真实示例：拉小程序在售商品 + 近 30 天订单销量，同步到飞书多维表格。核心是顶部的 `call_api()`，封装了**签名 + 请求**，调任何接口都复用它。
- `upload_goods.py` —— 真实示例：把商品上传到草稿箱（克隆已有商品 / 新建带本地图）。含一个踩坑实录：花城农夫只认自己和微购域名的图，本地图得走「litterbox→微购转存→szwego 永久 URL」中转，详见脚本头部注释。

## 用之前：填你自己的凭证

两个脚本顶部的占位符，换成你自己的（**别把真实值提交上来**）：

| 占位符 | 在哪个脚本 | 哪来的 |
|---|---|---|
| `PARTNER_ID` / `SECRET` | sync_products.py | 开通花城农夫开放平台后获得 |
| `FEISHU_*` | sync_products.py | 你的飞书自建应用凭证（只有用到飞书同步才需要） |
| `NF_PARTNER_ID` / `NF_SECRET` | upload_goods.py | 花城农夫开放平台凭证 |
| `WG_AUTH_KEY` / `WG_AUTH_SECRET` | upload_goods.py | 微购（微商相册）开放平台凭证，仅上传本地图时的中转才需要 |

## 接口调用速记

- 地址：`https://open.nfgood.com/api/v1/{方法名}`
- 方式：HTTPS POST + JSON，MD5 签名
- 签名串：`method=…&nonceStr=…&partnerId=…&timestamp=…&{body的json}&{secret}` → MD5 → 转大写
- 返回：正确给 `data`，错误给 `errors`
- 接口清单和参数详见 `花城小程序接口文档.md`

---

> ⚠️ 仅供学习参考。接口归花城农夫平台所有，使用请遵守平台开放协议。
