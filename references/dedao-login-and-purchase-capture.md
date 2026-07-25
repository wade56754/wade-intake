# 得到课程登录态与已购正文抓取要点

适用：`dedao.cn/course/detail?id=...` 课程详情页，尤其是需要从公开目录升级到已购正文抓取时。

## 关键判断

1. 先区分“公开课程目录”与“已购正文”。公开页可抓目录、标题、摘要与学习人数；正文通常需要登录且购买态成立。
2. 抓取前必须记录授权态证据：
   - 页面顶部是否仍显示“登录 / 注册”；
   - 主按钮是否仍显示“购买 : xx 元”；
   - `/pc/bauhinia/pc/class/info` 返回的 `class_info.is_subscribe` 是否为 `1`。
3. 若 `is_subscribe=0` 或页面仍显示购买按钮，只能交付“公开目录 + 待补正文”，不要声称已抓到已购正文。

## 登录方式与凭据边界

得到网页版课程页当前常见登录弹窗是「验证码登录 + 扫码登录」，不一定暴露账号密码登录入口。处理登录时：

1. 先枚举可见 `input` 与按钮，确认是否真的存在 `password`/密码框；不要把用户给的账号密码直接填进“验证码”输入框。
2. 若页面只有手机号 + 验证码：填手机号后点击「获取验证码」，让 Wade 提供短信验证码，再登录。
3. 若用户提供账号密码但页面无密码入口，简短说明“当前页不支持密码登录/未暴露入口，需要短信验证码或扫码”，不要在回复里复述完整密码。
4. 登录后必须核验顶部是否不再显示“登录 / 注册”、课程按钮是否不再显示“购买”，并用接口 `class_info.is_subscribe` 交叉验证。

## 登录二维码处理

得到登录弹窗里“扫码登录”二维码可能是 `data:image/png;base64,...` 的 `<img>`，而不是普通网络 URL。

浏览器里可用以下探针定位二维码：

```js
[...document.images].map((img,i)=>({
  i,
  src: img.src,
  alt: img.alt,
  w: img.naturalWidth,
  h: img.naturalHeight,
  rect: (() => { const r = img.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; })()
})).filter(x => x.rect.w > 20 || x.rect.h > 20)
```

扫码登录二维码的识别信号：
- 登录弹窗文案包含“扫码登录”“同时支持「得到App」和「微信」扫码”；
- 二维码图片通常是 `data:image/png;base64,...`；
- 页面可能还会有底部“得到App / 得到公众号”二维码，不要误把底部下载二维码当登录二维码；登录二维码一般在登录弹窗右侧上方。

如果当前对话通道没有图片附件能力，应给 Wade 一个本地截图路径或二维码截图路径，并说明页面位置；不要只说“我发你二维码”。

## 接口探测顺序

在用户扫码登录后，刷新课程页并依次探测：

1. `/pc/bauhinia/pc/class/info`
   - 参数：`{detail_id, is_login: 1}`
   - 用于确认 `class_info.is_subscribe`、`chapter_list`、公开元数据。
2. `/pc/bauhinia/pc/class/purchase/info`
   - 参数：`{detail_id, reverse:false}`
   - 登录/已购态下用于获取购买态章节结构。
3. `/pc/bauhinia/pc/class/purchase/article_list`
   - 参数示例：

```json
{
  "chapter_id": "3760",
  "count": 30,
  "detail_id": "eN7ndm2ploEVb1aHm9KA48zLBYG1vq",
  "include_edge": false,
  "is_unlearn": false,
  "max_id": 0,
  "max_order_num": 0,
  "reverse": false,
  "since_id": 0,
  "since_order_num": 0,
  "unlearn_switch": false
}
```

4. 若课程 `product_type` 为 `24` 以外，前端也可能走 `/api/pc/bauhinia/pc/class/purchase/article_list`。但接口可返回 HTML/鉴权错误，需以浏览器会话中的真实响应为准。

## 已购正文抓取实战路径（bb browser / CDP）

登录成功后，不要只重复调用未登录态接口。实测稳定链路是：

1. 用页面证据确认授权态：课程页显示「继续学习」，不显示「登录/注册」或「购买」。
2. 用接口交叉验证，参数必须用 `detail_id`，不要用 `id`：

```js
fetch('/pc/bauhinia/pc/class/info', {
  method: 'POST', credentials: 'include',
  headers: {'content-type': 'application/json'},
  body: JSON.stringify({detail_id: COURSE_ENID, is_login: 1})
})
```

`id: COURSE_ENID` 会返回 `服务异常`，不是未登录的充分证据。

3. 若 `/pc/bauhinia/pc/class/purchase/article_list` 仍返回异常，先在浏览器里点击任意可见课时，让前端初始化课程阅读状态；随后优先调用带 `/api` 前缀的接口：

```js
fetch('/api/pc/bauhinia/pc/class/purchase/article_list', {
  method: 'POST', credentials: 'include',
  headers: {'content-type': 'application/json'},
  body: JSON.stringify({
    chapter_id: '',
    count: 30,
    detail_id: COURSE_ENID,
    include_edge: false,
    is_unlearn: false,
    max_id: 0,
    max_order_num: 0,
    reverse: false,
    since_id: 0,
    since_order_num: 0,
    unlearn_switch: false
  })
})
```

4. 分页继续抓取时，用上一页最后一条的 `id` 和 `order_num` 作为 `max_id` / `max_order_num`。不要把第一页 30 条当完整课程。
5. 列表返回每讲 `enid` 后，逐条打开：

```text
https://www.dedao.cn/course/article?id={enid}
```

用浏览器可见 `document.body.innerText` / Playwright `body.inner_text()` 抓正文。正文页会触发 `/pc/ddarticle/v1/article/get/v2?...`，但接口响应可能只含元数据；以页面可见文本为准。
6. 特别放送、词典领取、直播笔记等资源页可能只有短提示或非标准正文，应标记为 `non_article_or_needs_followup`，不要硬凑成完整正文。
7. 落库建议：把逐讲原文保存为 raw layer；课程总览生成 Obsidian wikilinks 和 manifest，后续再按模块提炼知识卡片。

## 交付规则

- 已登录但网页仍提示“请在得到 App 中查看”时，记录为平台限制，不要编造正文。
- 若只抓到标题、summary、音频元数据，Obsidian 先落“课程总览 + 目录 + 待补正文状态”；正文获取另起 App/导出/授权流程。
- 若已抓到已购正文，必须报告：授权态证据、分页条数、完整正文条数、短页/特殊页条数、Obsidian 总览路径和 manifest 路径。
- 用户主动要求“打开登录界面我来登录”时，立即打开课程页登录弹窗并停住；登录完成后再继续抓取，不要让用户自己寻找入口。
