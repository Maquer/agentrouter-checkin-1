# AgentRouter 自动签到脚本 (青龙面板)

[AgentRouter](https://agentrouter.org) 每日自动签到脚本。签到 = 每日完成一次登录；登录响应 `checked_in=true` 即完成签到并下发当日额度。

> 本站支持**账号密码登录**（推荐，最稳）与 **GitHub OAuth 登录**（兜底）。脚本优先走账号密码，未配置密码时自动回退 GitHub OAuth。社区里 `POST /api/user/checkin` 的旧脚本在本站已失效（返回 404）。

## 原理（已对线上接口实测）

### 方式 A：账号密码登录（主路径，推荐）
向 `POST /api/user/login` 发送 `{username: 邮箱, password: 密码}`，服务端下发 session cookie，登录响应 `data.checked_in=true` 即签到成功，余额直接在 `data.quota` 中返回。密码是固定的，不像 GitHub 会话 cookie 会过期。

### 方式 B：GitHub OAuth 登录（兜底）
1. `GET /api/oauth/state?mode=login` 获取一次性 `state`
2. 用你的 GitHub 会话 cookie 访问 GitHub 授权页，因已授权过本站应用，GitHub 直接 302 跳回并带 `code`
3. `GET /api/oauth/github?code=...&state=...&mode=login` 换本站 session，`checked_in=true` 即签到成功

仅依赖 `requests`，无需浏览器自动化、无需解验证码。

## 配置（环境变量）

| 变量 | 必填 | 说明 |
|---|---|---|
| `AGENTROUTER_EMAIL` | 单账号必填（方式 A） | 你的注册邮箱，例：`zj773075692@gmail.com` |
| `AGENTROUTER_PASSWORD` | 单账号必填（方式 A） | 该账号在 AgentRouter 的登录密码 |
| `AGENTROUTER_GITHUB_COOKIE` | 单账号必填（方式 B） | 你 GitHub 账号的会话 cookie 整串（F12 → Network → 任意 github.com 请求的 `cookie:` 请求头，整行复制） |
| `AGENTROUTER_ACCOUNTS` | 可选 | 多账号 JSON 数组，每项 `{"name":"...","email":"...","password":"..."}` 或 `{"name":"...","github_cookie":"..."}`；设置后忽略单账号变量 |
| `AGENTROUTER_BASE_URL` | 可选 | 默认 `https://agentrouter.org`，备用 `https://ps.air-outer.com` |
| `AGENTROUTER_PROXY` | 可选 | 无法直连时填代理，如 `http://127.0.0.1:10808`（Docker 同机用 `http://host.docker.internal:10808`，http 不通试 `socks5://`） |
| `AGENTROUTER_FORCE_IPV4` | 可选 | 容器有 IPv6 但无 IPv6 路由导致 `Network unreachable` 时，设为 `1` 强制走 IPv4 |

## 青龙部署

1. 把 `agentrouter_checkin.py` 放到青龙 `scripts` 目录
2. 环境变量中设置 `AGENTROUTER_EMAIL` + `AGENTROUTER_PASSWORD`（推荐）或 `AGENTROUTER_GITHUB_COOKIE`
3. 新建任务：命令 `task agentrouter_checkin.py`，Cron `0 9 * * *`（每天 9 点；重复跑不重复发额度）

## 注意事项

- **账号密码方式基本一劳永逸**：密码不会像 GitHub 会话 cookie 那样过期，最省心。
- GitHub cookie 方式会过期（数天~数十天），过期后脚本报「GitHub 未登录」，重新从浏览器复制替换即可。
- 签到成功 ≠ 余额一定增加：实际发放额度由本站管理员按用户组配置的每日配额决定（普通用户可能为 0，但签到记录有效）。
- 账号密码 / cookie 属于登录凭证，请仅放在青龙环境变量中，不要写进脚本或提交到仓库。
