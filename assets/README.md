# assets

分享素材目录。

| 文件 | 用途 |
|---|---|
| `persona-poster.html` | **人格海报生成器**。单文件，浏览器打开即可预览，支持一键下载 1080×1440 PNG 与复制到剪贴板。替换 HTML 内的文本即可换成自己的画像内容。 |
| `preview.html` | 早期情报简报宣传图，保留供参考。 |

## 换成你自己的画像

1. 用 `scripts/persona_builder.py` 生成你的画像输出
2. 打开 `persona-poster.html`，替换以下几处：

| 要改的 | 位置 |
|---|---|
| 人格名 | `<div class="persona">` |
| Slogan | `<div class="slogan">` |
| 三个特质标签与解读 | `.trait` 区块 |
| 口味雷达条形图 | `.bar-row`，改 `style="width:XX%"` 与 `.bar-num` |
| 四项数据档案 | `.stat` 区块 |
| GitHub 地址 | `.repo` |

3. 点击「下载图片」得到 1080×1440 PNG

## 建议补的截图

放到 README 的「示例展示」章节效果更好：

- 人格海报成品
- 口味雷达区域特写
- 在 WorkBuddy 中的实际对话截图