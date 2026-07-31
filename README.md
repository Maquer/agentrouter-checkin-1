# AgentRouter 自动签到脚本 (青龙面板)

基于 GitHub OAuth 的 [AgentRouter](https://agentrouter.org) 每日自动签到脚本。
签到 = 每日用 GitHub 账号登录一次；登录响应 `checked_in=true` 即完成签到并下发当日额度。

> 本站目前已**只开放 GitHub / LinuxDO 两种 OAuth 登录**（无邮箱密码登录），故脚本走 GitHub OAuth 流程自动完成登录即签到。社区里 `POST /api/user/checkin` 的旧脚本在本站已失效（返回 404）。

## 原理（已对线上接口实测）

1. `GET /api/oauth/state?mode=login` 获取一次性 `state`
2. 用你的 GitHub 会话 cookie 访问 GitHub 授权页，因已授权过本站应用，GitHub 直接 302 跳回并带 `code`
3. `GET /api/oauth/github?code=...&state=...&mode=login` 换本站 session，`checked_in=true` 即签到成功

仅依赖 `requests`，无需浏览器自动化、无需解验证码。

## 配置（环境变量）

| 变量 | 必填 | 说明 |
|---|---|---|
| `AGENTROUTER_GITHUB_COOKIE` | 单账号必填 | 你 GitHub 账号的会话 cookie 整串（F12 → Network → 任意 github.com 请求的 `cookie:` 请求头，整行复制） |
| `AGENTROUTER_ACCOUNTS` | 可选 | 多账号 JSON 数组，每项 `{"name":"...","github_cookie":"..."}`；设置后忽略单账号变量 |
| `AGENTROUTER_BASE_URL` | 可选 | 默认 `https://agentrouter.org`，备用 `https://ps.air-outer.com` |
| `AGENTROUTER_PROXY` | 可选 | 无法直连时填代理，如 `http://127.0.0.1:10808`（Docker 同机用 `http://host.docker.internal:10808`，http 不通试 `socks5://`） |
| `AGENTROUTER_FORCE_IPV4` | 可选 | 容器有 IPv6 但无 IPv6 路由导致 `Network unreachable` 时，设为 `1` 强制走 IPv4 |

## 青龙部署

1. 把 `agentrouter_checkin.py` 放到青龙 `scripts` 目录
2. 环境变量中设置 `AGENTROUTER_GITHUB_COOKIE`
3. 新建任务：命令 `task agentrouter_checkin.py`，Cron `0 9 * * *`（每天 9 点；重复跑不重复发额度）

## 注意事项

- **GitHub cookie 会过期**（数天~数十天），过期后脚本报「GitHub 未登录」，重新从浏览器复制替换即可。
- 若 GitHub 开启两步验证且本次会话需重新验证，自动化会失败并提示需手动处理。
- 签到成功 ≠ 余额一定增加：实际发放额度由本站管理员按用户组配置的每日配额决定（普通 OAuth 用户可能为 0，但签到记录有效）。
- cookie 属于登录凭证，请仅放在青龙环境变量中，不要写进脚本或提交到仓库。
