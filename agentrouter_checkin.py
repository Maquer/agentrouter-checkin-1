#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentRouter 自动签到脚本 (青龙面板 / 任意 Python3 环境) — GitHub OAuth 版
站点: https://agentrouter.org

===== 原理 (已对线上 JS / 接口逐项实测确认) =====
本站现已【只开放 GitHub / LinuxDO 两种 OAuth 登录】(服务端 /api/status 确认),
没有邮箱密码登录。而"签到"= 每日登录一次:
  1) GET  /api/oauth/state?mode=login        -> 拿到一次性 state(token, 带过期)
  2) GET  https://github.com/login/oauth/authorize
          ?client_id=<本站GitHub应用ID>&state=<state>&scope=user:email
     用你自己的 GitHub 会话 cookie 访问; 因你此前已授权过该应用, GitHub 会 302
     跳回本站回调并带上 code
  3) GET  /api/oauth/github?code=<code>&state=<state>&mode=login
     -> 本站用 code 换 session, 并在 data.checked_in=true 时发放当日额度
所以脚本每天走一遍上述 OAuth 流程即完成签到, 无需邮箱密码, 也无需浏览器。

===== 配置方式 =====
方式一 (单账号, 推荐先跑通):
  AGENTROUTER_GITHUB_COOKIE  必填. 你 GitHub 账号的会话 cookie 整串。
    获取: 浏览器登录 github.com -> F12 -> Network/Application -> Cookies,
    复制全部 cookie (至少含 logged_in / dotcom_user / user_session / _octo 等)。

方式二 (多账号):
  AGENTROUTER_ACCOUNTS 选填. JSON 数组, 例:
  [
    {"name":"账号甲","github_cookie":"logged_in=yes; user_session=xxx; ..."},
    {"name":"账号乙","github_cookie":"logged_in=yes; user_session=yyy; ..."}
  ]
  设置了此项会自动忽略方式一的单账号变量.

===== 青龙定时 =====
  新建任务 -> 命令: task agentrouter_checkin.py
  Cron: 0 9 * * *   (每天上午 9 点; 重复跑不会重复发额度, 服务端按天去重)
  依赖: requests (青龙面板自带; 本地缺则 pip install requests)

===== 注意事项 =====
  * GitHub 会话 cookie 会过期(数天~数十天), 过期后脚本会报"GitHub 未登录",
    届时重新从浏览器复制一次即可。
  * 若 GitHub 开启了两步验证且本次会话需重新验证, 自动化会失败并提示需手动处理。
  * 备用域名 ps.air-outer.com 与本域名功能一致, 如需可改 BASE_URL。
  * 若青龙环境无法直连 agentrouter.org / github.com(需翻墙), 设环境变量
    AGENTROUTER_PROXY 指向可达代理, 例如 http://127.0.0.1:10808
    (Docker 同机用 http://host.docker.internal:10808; http 不通试 socks5://)。
"""

import os
import sys
import json
import time
import random
import traceback
from urllib.parse import urlparse, parse_qs

try:
    import requests
except ImportError:
    print("缺少依赖 requests, 请先执行: pip install requests")
    sys.exit(1)

# ---------- 基础配置 ----------
BASE_URL = os.environ.get("AGENTROUTER_BASE_URL", "https://agentrouter.org").rstrip("/")
STATE_PATH = "/api/oauth/state"
GITHUB_EXCHANGE_PATH = "/api/oauth/github"
SELF_PATH = "/api/user/self"
GITHUB_AUTHZ = "https://github.com/login/oauth/authorize"
TIMEOUT = 20
# 已知 client_id (运行时也会从 /api/status 动态刷新, 这里作兜底)
GITHUB_CLIENT_ID_FALLBACK = "Ov23lidtiR4LeVZvVRNL"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# ---------- 代理(可选) ----------
# 若青龙环境无法直接访问 agentrouter.org / github.com(常见于需翻墙的环境),
# 设 AGENTROUTER_PROXY 指向一个可达的代理, 例如:
#   本机原生运行:  http://127.0.0.1:10808
#   Docker 同机:   http://host.docker.internal:10808
#   若 http 不通:  socks5://127.0.0.1:10808
# 不设置则沿用系统/环境自带的 HTTPS_PROXY。
PROXY = os.environ.get("AGENTROUTER_PROXY", "").strip()
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

# ---------- 强制 IPv4(可选) ----------
# 部分容器有 IPv6 地址但无 IPv6 默认路由, 解析到站点 IPv6 地址后连接直接报
# [Errno 101] Network unreachable 且不回退 IPv4。设 AGENTROUTER_FORCE_IPV4=1
# 可强制所有连接只走 IPv4。海外服务器能直连时一般无需代理。
if os.environ.get("AGENTROUTER_FORCE_IPV4", "").strip() in ("1", "true", "yes", "on"):
    import socket as _socket
    _orig_getaddrinfo = _socket.getaddrinfo
    def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)
    _socket.getaddrinfo = _getaddrinfo_ipv4

# 通知: 青龙自带 notify 模块, 没有则降级为仅打印
send = None
try:
    from notify import send  # 青龙面板内置
except Exception:
    send = None


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}] {msg}")


def safe_notify(title, content):
    if send:
        try:
            send(title, content)
        except Exception as e:
            log(f"通知发送失败(不影响签到): {e}")
    else:
        log(f"[通知] {title}\n{content}")


def parse_cookie(cookie_str):
    cookies = {}
    if not cookie_str:
        return cookies
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def get_github_client_id():
    try:
        r = requests.get(f"{BASE_URL}/api/status", timeout=TIMEOUT,
                         headers={"User-Agent": UA}, proxies=PROXIES)
        data = r.json().get("data", {})
        cid = data.get("github_client_id")
        if cid:
            return cid
    except Exception:
        pass
    return GITHUB_CLIENT_ID_FALLBACK


def get_state():
    """获取一次性 state token。失败返回 (None, 错误)"""
    try:
        r = requests.get(f"{BASE_URL}{STATE_PATH}?mode=login", timeout=TIMEOUT,
                         headers={"User-Agent": UA, "Referer": f"{BASE_URL}/login",
                                  "Origin": BASE_URL}, proxies=PROXIES)
        if "text/html" in r.headers.get("Content-Type", ""):
            return None, "state 接口返回 HTML(可能被 WAF 拦截)"
        j = r.json()
        if j.get("success"):
            return j.get("data"), None
        return None, j.get("message") or "获取 state 失败"
    except Exception as e:
        return None, f"获取 state 异常: {e}"


def github_authorize(state, github_cookie, client_id):
    """用 GitHub 会话 cookie 走授权, 返回 (code, error)。"""
    gh = requests.Session()
    gh.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    gh.proxies = PROXIES
    for k, v in parse_cookie(github_cookie).items():
        gh.cookies.set(k, v)

    url = (f"{GITHUB_AUTHZ}?client_id={client_id}&state={state}&scope=user:email")
    try:
        r = gh.get(url, timeout=TIMEOUT, allow_redirects=False)
    except Exception as e:
        return None, f"访问 GitHub 授权页异常: {e}"

    # 302/303 -> 检查 Location 是否带 code
    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("Location", "")
        if "code=" in loc:
            q = parse_qs(urlparse(loc).query)
            code = q.get("code")
            if code:
                return code[0], None
        # 跳回 github 登录/2fa -> 会话失效或未授权
        return None, "GitHub 未登录/需 2FA 或尚未授权本站应用(code 未返回)"
    # 200 且是 HTML -> GitHub 要求手动点击"Authorize"或输入 2FA
    if "text/html" in r.headers.get("Content-Type", ""):
        return None, "GitHub 返回授权页面(需手动授权/2FA), 请先在浏览器用该 GitHub 账号完成一次本站授权"
    # 其他
    return None, f"GitHub 授权页意外响应: HTTP {r.status_code}"


def exchange(code, state):
    """用 code+state 换本站 session。返回 (site_session, json, error)。"""
    site = requests.Session()
    site.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE_URL}/oauth/github",
        "Origin": BASE_URL,
    })
    site.proxies = PROXIES
    try:
        r = site.get(f"{BASE_URL}{GITHUB_EXCHANGE_PATH}",
                     params={"code": code, "state": state, "mode": "login"},
                     timeout=TIMEOUT)
        if "text/html" in r.headers.get("Content-Type", ""):
            return None, None, "换 session 接口返回 HTML(可能被 WAF 拦截)"
        return site, r.json(), None
    except Exception as e:
        return None, None, f"换 session 异常: {e}"


def get_self_quota(site_session):
    try:
        r = site_session.get(f"{BASE_URL}{SELF_PATH}", timeout=TIMEOUT)
        if "text/html" in r.headers.get("Content-Type", ""):
            return None
        data = r.json()
        payload = data.get("data") if isinstance(data, dict) else None
        if isinstance(payload, dict):
            for k in ("quota", "remainder_quota", "balance"):
                if k in payload:
                    return payload[k]
    except Exception:
        pass
    return None


def do_checkin(account, client_id):
    name = account.get("name", "默认账号")
    github_cookie = account.get("github_cookie", "").strip()
    if not github_cookie:
        return _result(name, "fail", "未配置 github_cookie, 跳过", None, None)

    log(f"====== 开始处理账号: {name} ======")

    # 1) state
    state, err = get_state()
    if err:
        return _result(name, "fail", f"获取 state 失败: {err}", None, None)

    # 2) github authorize -> code
    code, err = github_authorize(state, github_cookie, client_id)
    if err:
        return _result(name, "fail", f"GitHub 授权失败: {err}", None, None)

    # 3) exchange -> session + checkin result
    site_session, j, err = exchange(code, state)
    if err:
        return _result(name, "fail", f"换 session 失败: {err}", None, None)
    if not isinstance(j, dict) or not j.get("success"):
        msg = (j or {}).get("message") or "换 session 失败"
        return _result(name, "fail", f"登录失败: {msg}", None, None)

    payload = j.get("data") or {}
    checked_in = bool(payload.get("checked_in"))
    username = payload.get("username") or payload.get("display_name") or ""

    # 4) 确认额度
    quota = None
    for k in ("quota", "remainder_quota", "balance"):
        if k in payload:
            quota = payload[k]
            break
    if quota is None and site_session is not None:
        quota = get_self_quota(site_session)

    if checked_in:
        status = "success"
        msg = "签到成功，新增额度已到账" if "已签到" not in (j.get("message") or "") else j.get("message")
    else:
        status = "success"
        msg = "登录成功，但 checked_in=false(可能今日额度已发或接口变化)"

    return _result(name, status, msg, username, quota)


def _result(name, status, message, username, quota):
    res = {
        "name": name,
        "status": status,
        "message": message,
        "username": username or "",
        "quota": quota,
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    tag = {"success": "✅ 成功", "already": "🟡 已签到", "fail": "❌ 失败"}[status]
    quota_str = f"{quota}" if quota is not None else "未知"
    log(f"[{name}] {tag} | {message} | 额度: {quota_str}")
    return res


def collect_accounts():
    accounts = []
    multi = os.environ.get("AGENTROUTER_ACCOUNTS", "").strip()
    if multi:
        try:
            arr = json.loads(multi)
            if isinstance(arr, list):
                for i, a in enumerate(arr):
                    a.setdefault("name", f"账号{i + 1}")
                    accounts.append(a)
                log(f"已读取多账号配置, 共 {len(accounts)} 个")
                return accounts
        except Exception as e:
            log(f"AGENTROUTER_ACCOUNTS 解析失败: {e}, 回退到单账号")

    cookie = os.environ.get("AGENTROUTER_GITHUB_COOKIE", "").strip()
    if cookie:
        accounts.append({"name": "默认账号", "github_cookie": cookie})
        log("已读取单账号配置")
    else:
        log("未检测到任何配置: 请设置 AGENTROUTER_GITHUB_COOKIE 或 AGENTROUTER_ACCOUNTS")
    return accounts


def main():
    log("AgentRouter 自动签到启动 (GitHub OAuth 登录即签到)")
    client_id = get_github_client_id()
    log(f"GitHub client_id: {client_id}")

    accounts = collect_accounts()
    if not accounts:
        safe_notify("[AgentRouter] 签到失败", "未检测到账号配置, 请检查环境变量")
        return

    results = []
    for acc in accounts:
        try:
            res = do_checkin(acc, client_id)
            if res:
                results.append(res)
        except Exception:
            log(f"[{acc.get('name', '?')}] 处理异常:\n{traceback.format_exc()}")
        if len(accounts) > 1:
            time.sleep(random.uniform(2, 5))

    if not results:
        safe_notify("[AgentRouter] 签到失败", "所有账号均未成功执行")
        return

    lines = []
    for r in results:
        tag = {"success": "✅", "already": "🟡", "fail": "❌"}[r["status"]]
        quota_str = f"{r['quota']}" if r["quota"] is not None else "未知"
        who = r["username"] or r["name"]
        lines.append(f"{tag} {r['name']}({who})：{r['message']} | 额度 {quota_str}")
    safe_notify("[AgentRouter] 签到汇总", "\n".join(lines))
    log("全部账号处理完毕")


if __name__ == "__main__":
    main()
