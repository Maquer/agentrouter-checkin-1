# AgentRouter 自动签到脚本 (青龙面板)

[AgentRouter](https://agentrouter.org) 每日自动签到脚本。签到 = 每日完成一次登录；登录响应 `checked_in=true` 即完成签到并下发当日额度。

> 仅支持**账号密码登录**（最稳）。脚本优先走账号密码，登录成功后会再读一次个人日志做端到端核验，确认签到真的成功了。社区里 `POST /api/user/checkin` 的旧脚本在本站已失效（返回 404）。

## 原理（已对线上接口实测）

向 `POST /api/user/login` 发送 `{username: 邮箱, password: 密码}`，服务端下发 session cookie，登录响应 `data.checked_in=true` 即签到成功，余额直接在 `data.quota` 中返回。密码是固定的，不像第三方会话 cookie 那样会过期，基本一劳永逸。

仅依赖 `requests`，无需浏览器自动化、无需解验证码。

## 配置（环境变量）

| 变量 | 必填 | 说明 |
|---|---|---|
| `AGENTROUTER_ACCOUNT` | 单账号必填 | 格式 `邮箱#密码`，用 `#` 隔开，例：`zj773075692@gmail.com#你的密码` |
| `AGENTROUTER_ACCOUNTS` | 可选 | 多账号 JSON 数组，每项 `{"name":"备注","account":"邮箱#密码"}`；设置后忽略上面的单账号变量。例：`[{"name":"甲","account":"a@x.com#pwdA"},{"name":"乙","account":"b@x.com#pwdB"}]` |
| `AGENTROUTER_BASE_URL` | 可选 | 默认 `https://agentrouter.org`，备用 `https://ps.air-outer.com` |
| `AGENTROUTER_PROXY` | 可选 | 无法直连时填代理，如 `http://127.0.0.1:10808`（Docker 同机用 `http://host.docker.internal:10808`，http 不通试 `socks5://`） |
| `AGENTROUTER_FORCE_IPV4` | 可选 | 容器有 IPv6 但无 IPv6 路由导致 `Network unreachable` 时，设为 `1` 强制走 IPv4 |

## 青龙部署

1. 把 `agentrouter_checkin.py` 放到青龙 `scripts` 目录
2. 环境变量中设置 `AGENTROUTER_ACCOUNT`（格式 `邮箱#密码`）
3. 新建任务：命令 `task agentrouter_checkin.py`，Cron `0 9 * * *`（每天 9 点；重复跑不重复发额度）

## 签到核验（端到端保证）

登录返回 `checked_in=true` 只说明「当日签到已计入」，脚本还会再读一次个人日志接口 `GET /api/log/self/`（带 `New-API-User: <数字 uid>` 请求头）做二次确认：

- 在日志中找到 `type=4` 且内容含「签到成功」的记录（即控制台 `/console/log` 里的那条「每日签到成功，增加额度 …」），且时间在近 24 小时内，才会判定为 **日志已确认**；
- 若本次运行刚生成签到日志（数十秒内），判定为「本次运行已生成签到日志」，置信度最高；
- 若日志接口异常或找不到近期记录，仍会以登录结果为准，但会在汇总里提示「日志未确认」，此时建议手动到 `/console/log` 核对。

> 这是为了防止「登录接口成功了、但签到实际没触发」这类边界情况，确保每次跑完都能确定签到真的成功了。

## 注意事项

- **账号密码方式基本一劳永逸**：密码不会像第三方会话 cookie 那样过期，最省心。
- 签到成功 ≠ 余额一定增加：实际发放额度由本站管理员按用户组配置的每日配额决定（普通用户可能为 0，但签到记录有效）。
- 账号密码属于登录凭证，请仅放在青龙环境变量中（`AGENTROUTER_ACCOUNT`，用 `#` 隔开），不要写进脚本或提交到仓库。
