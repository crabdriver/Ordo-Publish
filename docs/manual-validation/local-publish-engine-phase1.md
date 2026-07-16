# ordo Phase 1 Manual Validation Matrix

日期：2026-03-27

## 目标

这份清单用于验证 `ordo` 一期本地发布引擎在真实平台环境中的最小可用路径，并记录当前已知风险。

默认前提：

- 已安装 Python 依赖
- 本机可用 Chrome 或 Chromium
- 浏览器平台已启用远程调试
- 各目标平台已有有效登录态

## 微信

### 最小成功路径

1. 确认 `secrets.env` 已配置 `VPS_IP`、`VPS_USER`、`VPS_PATH`。
2. 本机不得直接运行 `wechat_publisher.py`。
3. 通过统一入口运行单篇草稿发布：
   `python3 publish.py "./my_articles/example.md" --platform wechat --mode draft --wechat-theme chinese`
4. 确认输出包含 `已写入微信公众号草稿`

### 失败诊断路径

1. 移除或改错 `WECHAT_APPID` / `WECHAT_SECRET`
2. 再次执行草稿发布
3. 确认报错信息能指出凭证问题

## 知乎

### 最小成功路径

1. 在远程调试 Chrome 中打开并登录知乎写作页
2. 运行：
   `python3 publish.py "./my_articles/example.md" --platform zhihu --mode draft`
3. 确认标题写入、正文注入成功
4. 确认输出包含 `已写入知乎草稿页`

### 失败诊断路径

1. 关闭或登出知乎标签页
2. 再次运行相同命令
3. 确认输出能提示工作台标签页或编辑器未就绪

## 头条号

### 最小成功路径

1. 在远程调试 Chrome 中打开并登录头条号发文页
2. 运行：
   `python3 publish.py "./my_articles/example.md" --platform toutiao --mode draft`
3. 确认标题、正文、封面模式流程可执行
4. 确认输出包含 `已写入头条草稿页`

### 失败诊断路径

1. 刻意切到非编辑页或移除必要标签页
2. 重新执行命令
3. 确认能识别编辑器未就绪或标签页缺失

## 简书

### 最小成功路径

1. 在远程调试 Chrome 中打开并登录简书写作后台
2. 运行：
   `python3 publish.py "./my_articles/example.md" --platform jianshu --mode draft`
3. 确认能进入编辑器、创建文章并生成草稿
4. 确认输出包含 `已生成简书草稿`

### 失败诊断路径

1. 在正式发布模式下制造简书当日上限条件或使用预检模拟
2. 运行：
   `python3 publish.py "./my_articles/example.md" --platform jianshu --mode publish`
3. 确认输出或预检能提示发布上限

## 一点号

### 最小成功路径

1. 在远程调试 Chrome 中打开并登录一点号发文页
2. 运行：
   `python3 publish.py "./my_articles/example.md" --platform yidian --mode draft`
3. 确认能进入编辑器、写入标题正文、保存草稿
4. 确认输出包含 `已存草稿`

### 失败诊断路径

1. 切到内容管理页或非编辑态
2. 再次执行命令
3. 确认程序能尝试切回编辑器，失败时给出明确提示

## 非微信平台封面池与 CLI 元数据

### 统一封面与自动校验

- `ordo-scribe` 发布包只使用 `assets/<article_id>/cover.png`；`platform_covers` 六个平台都指向同一张图。
- 文件必须是 PNG、sRGB、精确 `2538x1080`、`2.35:1`，不超过 `5 MB`。
- 中央 `1920x1080` 是 16:9 安全区，中央约 `1600x800` 是核心安全区；左右各 `309 px` 只放可裁剪背景，核心主体距左右边缘至少 `350 px`。
- 禁止标题、Logo、水印及任何可见文字。禁止放大旧低分辨率图片，只允许从更大的高分辨率源图裁切和降采样。
- `draft` 和 `publish` 模式都会在发布前校验封面；缺失或不合格时 **BLOCK**，不再从封面池随机替换。

### GUI-ready 结构化输出

- 每条平台结果在 `[EXIT]` 之后应出现一行 `[META] { ... }` JSON，包含：`article_id`、`theme_name`、`template_mode`、`cover_path`、`platform`、`status`、`error_type`。
- `publish_records.csv` 应包含与上述一致的列；从旧版 CSV 升级时，首次写入可能触发自动列迁移，验证前请自行备份该文件。

### 浏览器自定义封面（现状）

| 平台   | 自定义封面（引擎传入路径） | 备注 |
|--------|-----------------------------|------|
| 知乎   | 支持尝试                    | 依赖写作页 DOM 与 CDP |
| 头条号 | 支持尝试                    | 同上 |
| 一点号 | 支持尝试                    | 同上 |
| 简书   | 易失败                      | 当前以**显式诊断失败**为主，不视为已全面支持简书封面上传 |

调试上传控件时，可在远程调试会话中对已知 `input[type=file]` 使用：

`node live_cdp.mjs setfile <target> "<css-selector>" "/path/to/cover.png"`

（与主发布脚本内部用法一致；是否选中正确控件需结合页面实际结构。）

## 批量与恢复

### 最小成功路径

1. 运行：
   `python3 publish.py "./my_articles" --platform all --mode draft --continue-on-error`
2. 确认多篇文章可以逐篇执行
3. 确认 `publish_records.csv` 有结构化记录（含 GUI 元数据列时更易对照会话）
4. 如使用控制台模式，确认 `.ordo/publish-console/` 中的会话文件持续更新

### 失败诊断路径

1. 在批量执行过程中故意让某个平台失败
2. 确认其他平台或后续文章在 `--continue-on-error` 下继续执行
3. 确认失败项在状态文件和记录文件中可追踪

## 已知风险

- 浏览器平台依赖真实 DOM，平台改版会直接影响自动化稳定性
- 简书自定义封面与编辑器限制耦合，自动化测试不替代真实页面回归
- 目前内部缓存目录仍沿用 `.ordo/`，后续可能再迁移命名
- `scripts/format.py` 仍是较大的独立工具文件，本期未做深度拆分
- 真实平台验证仍需要人工回归，自动化测试目前主要覆盖模型、状态和调度契约
