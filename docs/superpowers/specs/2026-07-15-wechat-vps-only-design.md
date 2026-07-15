# 微信公众号 VPS-Only 发布设计

## 目标

微信公众号草稿创建只能由 VPS 固定公网 IP 发起。本机不得直接调用微信 API；配置缺失、执行环境不明时必须 fail closed。

## 执行边界

- 本机：解析任务、校验发布包、通过 SSH/SCP 上传文章和封面、接收 VPS 结果。
- VPS：设置 `ORDO_WORKER=1`，运行 `wechat_publisher.py`，调用微信 API。
- 浏览器平台：继续使用本机隔离 owned browser，不受微信路线影响。
- 微信路线不启动浏览器，不运行 CDP 预检。

## 入口规则

- `publish.py --platform wechat`：自动使用专用 VPS 微信适配器。
- `BatchCoordinator`：通过同一适配器执行微信，不得直接 spawn 本地 worker。
- 本机直接运行 `wechat_publisher.py`：立即退出，不读取 token、不调用 API。
- VPS 缺少 `VPS_IP`、上传失败、SSH 失败：明确失败，不回退本地。

## 状态与重复保护

- 只有 VPS 输出明确草稿 media id 才记录 `draft_saved`。
- SSH 超时或结果不明确记 `manual_verify`，不得自动重投。
- 已有幂等状态继续生效；禁止 `--force` 自动重发。

## 删除内容

- 删除 `WECHAT_PROXY` 作为本地微信发表路线。
- 删除“微信允许本机 subprocess 发布”的测试和行为。
- 文档不得再宣称微信默认本机执行。

## 测试

- 本机 adapter 无 VPS 配置必须阻断。
- adapter 必须构造 SSH/SCP，并在远端设置 `ORDO_WORKER=1`、清除代理变量。
- BatchCoordinator 必须委托 VPS adapter，不得调用本地 `wechat_publisher.py`。
- worker 非 VPS 环境必须在任何 API 调用前退出。
