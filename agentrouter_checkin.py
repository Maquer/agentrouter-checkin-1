#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentRouter 自动签到脚本 (青龙面板 / 任意 Python3 环境)
站点: https://agentrouter.org

===== 原理 (已对线上接口逐项实测确认) =====
本站"签到"= 每日完成一次登录。支持两种登录方式:

方式 A [账号密码登录, 推荐/主路径, 实测最稳]:
  向 POST /api/user/login 发送 {username: 邮箱, password: 密码}
  -> 服务端下发 session cookie, 并在 data.checked_in=true 时发放当日额度
  -> 登录响应 data 里直接带 quota(余额), 无需额外查询。
  优点: 密码是固定的, 不像 GitHub 会话 cookie 会过期, 基本一劳永逸。

方式 B [GitHub OAuth 登录, 兜底]:
  1) GET  /api/oauth/state?mode=login        -> 拿到一次性 state
  2) GET  https://github.com/login/oauth/authorize?client_id=<本站应用ID>&state=<state>&scope=user:email
     用你的 GitHub 会话 cookie 访问; 因你此前已授权过该应用, GitHub 会 302 跳回本站回调并带 code
  3) GET  /api/oauth/github?code=<code>&state=<state>&mode=login
     -> 本站用 code 换 session, 并在 data.checked_in=true 时发放当日额度
  缺点: GitHub 会话 cookie 会过期, 过期需重新复制。

脚本优先使用方式 A; 若账号只配了 GitHub cookie 则自动走方式 B。

===== 配置方式 =====
单账号(账号密码, 推荐):
  AGENTROUTER_EMAIL      必填. 你的注册邮箱, 例: zj773075692@gmail.com
  AGENTROUTER_PASSWORD   必填. 该账号在 AgentRouter 的登录密码

单账号(GitHub OAuth, 兜底):
  AGENTROUTER_GITHUB_COOKIE  必填. 你 GitHub 账号的会话 cookie 整串
     (至少含 logged_in / dotcom_user / user_session / _octo)

多账号(混用也行):
  AGENTROUTER_ACCOUNTS 选填. JSON 数组, 例:
  [
    {"name":"账号甲","email":"a@x.com","password":"pwdA"},
    {"name":"账号乙","github_cookie":"logged_in=yes; user_session=xxx; ..."}
  ]
  设置了此项会自动忽略上面的单账号变量。

===== 青龙定时 =====
  新建任务 -> 命令: task agentrouter_checkin.py
  Cron: 0 9 * * *   (每天上午 9 点; 重复跑不会重复发额度, 服务端按天去重)
  依赖: requests (青龙面板自带; 本地缺则 pip install requests)

===== 注意事项 =====
  * 账号密码方式无需担心 cookie 过期, 最省心。
  * GitHub cookie 方式会过期(数天~数十天), 过期后脚本报"GitHub 未登录", 重新复制即可。
  * 备用域名 ps.air-outer.com 与本域名功能一致, 如需可改 AGENTROUTER_BASE_URL。
  * 若青龙环境无法直连(常见于需翻墙/容器 IPv6 问题):
    - 设 AGENTROUTER_FORCE_IPV4=1 强制走 IPv4 (海外服务器直连常见修复)
    - 或设 AGENTROUTER_PROXY 指向可达代理, 例 http://127.0.0.1:10808
      (Docker 同机用 http://host.docker.internal:10808; http 不通试 socks5://)
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
LOGIN_PATH = "/api/user/login"
STATE_PATH = "/api/oauth/state"
GITHUB_EXCHANGE_PATH = "/api/oauth/github"
GITHUB_AUTHZ = "https://github.com/login/oauth/authorize"
TIMEOUT = 20
# 已知 client_id (运行时也会从 /api/status 动态刷新, 这里作兜底)
GITHUB_CLIENT_ID_FALLBACK = "Ov23lidtiR4LeVZvVRNL"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# ---------- 代理(可选) ----------
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


def extract_quota(payload):
    """从登录/self 响应的 data 中提取余额字段。"""
    if isinstance(payload, dict):
        for k in ("quota", "remainder_quota", "balance"):
            if k in payload:
                return payload[k]
    return None


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


# ===================== 方式 A: 账号密码登录 =====================
def password_login(account):
    name = account.get("name", "默认账号")
    email = (account.get("email") or "").strip()
    password = (account.get("password") or "").strip()
    if not email or not password:
        return _result(name, "fail", "未配置 email/password, 跳过", None, None)

    log(f"====== 开始处理账号(账号密码登录): {name} ======")
    site = requests.Session()
    site.headers.update({
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE_URL}/login",
        "Origin": BASE_URL,
    })
    site.proxies = PROXIES

    try:
        r = site.post(f"{BASE_URL}{LOGIN_PATH}",
                      json={"username": email, "password": password},
                      timeout=TIMEOUT)
    except Exception as e:
        return _result(name, "fail", f"登录请求异常: {e}", None, None)

    if "text/html" in r.headers.get("Content-Type", ""):
        return _result(name, "fail", "登录接口返回 HTML(可能被 WAF 拦截或路径变化)", None, None)

    try:
        j = r.json()
    except Exception:
        return _result(name, "fail", f"登录响应非 JSON: {r.text[:120]}", None, None)

    if not j.get("success"):
        return _result(name, "fail",
                       f"登录失败: {j.get('message') or r.text[:120]}", None, None)

    data = j.get("data") or {}
    checked_in = bool(data.get("checked_in"))
    username = data.get("username") or data.get("display_name") or email
    quota = extract_quota(data)

    if checked_in:
        status = "success"
        msg = "签到成功，新增额度已到账" if "已签到" not in (j.get("message") or "") else j.get("message")
    else:
        status = "success"
        msg = "登录成功，但 checked_in=false(可能今日额度已发或接口变化)"

    return _result(name, status, msg, username, quota)


# ===================== 方式 B: GitHub OAuth 登录 =====================
def get_state():
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

    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("Location", "")
        if "code=" in loc:
            q = parse_qs(urlparse(loc).query)
            code = q.get("code")
            if code:
                return code[0], None
        return None, "GitHub 未登录/需 2FA 或尚未授权本站应用(code 未返回)"
    if "text/html" in r.headers.get("Content-Type", ""):
        return None, "GitHub 返回授权页面(需手动授权/2FA), 请先在浏览器用该 GitHub 账号完成一次本站授权"
    return None, f"GitHub 授权页意外响应: HTTP {r.status_code}"


def exchange(code, state):
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


def github_oauth_checkin(account, client_id):
    name = account.get("name", "默认账号")
    github_cookie = (account.get("github_cookie") or "").strip()
    if not github_cookie:
        return _result(name, "fail", "未配置 github_cookie, 跳过", None, None)

    log(f"====== 开始处理账号(GitHub OAuth): {name} ======")

    state, err = get_state()
    if err:
        return _result(name, "fail", f"获取 state 失败: {err}", None, None)

    code, err = github_authorize(state, github_cookie, client_id)
    if err:
        return _result(name, "fail", f"GitHub 授权失败: {err}", None, None)

    site_session, j, err = exchange(code, state)
    if err:
        return _result(name, "fail", f"换 session 失败: {err}", None, None)
    if not isinstance(j, dict) or not j.get("success"):
        msg = (j or {}).get("message") or "换 session 失败"
        return _result(name, "fail", f"登录失败: {msg}", None, None)

    payload = j.get("data") or {}
    checked_in = bool(payload.get("checked_in"))
    username = payload.get("username") or payload.get("display_name") or ""
    quota = extract_quota(payload)

    if checked_in:
        status = "success"
        msg = "签到成功，新增额度已到账" if "已签到" not in (j.get("message") or "") else j.get("message")
    else:
        status = "success"
        msg = "登录成功，但 checked_in=false(可能今日额度已发或接口变化)"

    return _result(name, status, msg, username, quota)


# ===================== 调度 =====================
def do_checkin(account, client_id):
    email = (account.get("email") or "").strip()
    password = (account.get("password") or "").strip()
    github_cookie = (account.get("github_cookie") or "").strip()
    if email and password:
        return password_login(account)
    elif github_cookie:
        return github_oauth_checkin(account, client_id)
    else:
        return _result(account.get("name", "默认账号"), "fail",
                       "账号未配置 email/password 或 github_cookie", None, None)


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

    email = os.environ.get("AGENTROUTER_EMAIL", "").strip()
    password = os.environ.get("AGENTROUTER_PASSWORD", "").strip()
    if email and password:
        accounts.append({"name": "默认账号", "email": email, "password": password})
        log("已读取单账号配置(账号密码登录)")
        return accounts

    cookie = os.environ.get("AGENTROUTER_GITHUB_COOKIE", "").strip()
    if cookie:
        accounts.append({"name": "默认账号", "github_cookie": cookie})
        log("已读取单账号配置(GitHub OAuth)")
        return accounts

    log("未检测到任何配置: 请设置 AGENTROUTER_EMAIL+AGENTROUTER_PASSWORD 或 AGENTROUTER_GITHUB_COOKIE")
    return accounts


def main():
    log("AgentRouter 自动签到启动 (账号密码 / GitHub OAuth 登录即签到)")
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
