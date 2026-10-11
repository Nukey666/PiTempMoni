# -*- coding: utf-8 -*-
"""窗口 / JS 桥验证：窗口能开、桥能通、错误能回传。

不靠肉眼看截图——在 webview.start(callback) 的回调里直接 evaluate_js 取值断言。

桥、空主机拦截这些不需要真机，直接跑就行。需要真机的部分（连上、密码错、
断开重连）要设 PI_HOST / PI_USER / PI_PASS，没设就自动跳过，不算失败。
"""
import json
import os
import sys
import time

import webview

import app as appmod

result = {}

# 真机信息一律走环境变量，不写进源码。
# PI_HOST / PI_USER 没给就跳过所有需要真机的检查（桥、空主机拦截仍然跑）。
HOST = os.environ.get("PI_HOST", "")
USER = os.environ.get("PI_USER", "")
PASSWORD = os.environ.get("PI_PASS", "")
HAVE_TARGET = bool(HOST and USER)


def js(window, code):
    return window.evaluate_js(code)


def fill(window, password):
    """把表单填好并点“连接”按钮，全部走页面自己的代码路径。"""
    js(window, """
      document.getElementById('host').value = %s;
      document.getElementById('user').value = %s;
      document.getElementById('pass').value = %s;
      document.getElementById('btn').click();
    """ % (json.dumps(HOST), json.dumps(USER), json.dumps(password)))


def snap(window, prefix):
    result[prefix + "status"] = js(window, "document.getElementById('status').textContent")
    result[prefix + "class"] = js(window, "document.getElementById('status').className")
    result[prefix + "btn"] = js(window, "document.getElementById('btn').textContent")
    result[prefix + "locked"] = js(window, "document.getElementById('host').disabled")


def probe(window):
    time.sleep(2.5)                       # 等页面加载 + pywebviewready

    result["bridge"] = js(window, "typeof window.pywebview")
    result["api_connect"] = js(window, "typeof window.pywebview.api.connect")
    result["api_disconnect"] = js(window, "typeof window.pywebview.api.disconnect")
    result["btn_enabled"] = js(window, "document.getElementById('btn').disabled === false")
    result["init_host"] = js(window, "document.getElementById('host').value")
    result["init_user"] = js(window, "document.getElementById('user').value")
    snap(window, "init_")

    # --- 空主机：直接点「连接」必须被拦下，给一句人话而不是抛异常 ---
    js(window, "document.getElementById('btn').click();")
    time.sleep(1.0)
    snap(window, "blank_")

    if not HAVE_TARGET:
        print("跳过需要真机的检查：请设置 PI_HOST / PI_USER / PI_PASS")
        window.destroy()
        return

    # --- 错误路径：密码故意写错 ---
    fill(window, "definitely-wrong")
    time.sleep(4.0)
    snap(window, "err_")

    # --- 成功路径：真密码从环境变量进来，不落盘 ---
    if PASSWORD:
        fill(window, PASSWORD)
        time.sleep(4.0)
        snap(window, "ok_")

        js(window, "document.getElementById('btn').click();")   # 再点一次 = 断开
        time.sleep(1.0)
        snap(window, "after_")

        # 断开后能再次连上（验收标准 5）
        fill(window, PASSWORD)
        time.sleep(4.0)
        snap(window, "re_")

    window.destroy()


api = appmod.Api()
window = webview.create_window(
    title="step2 check",
    url=os.path.join(appmod.BASE_DIR, "index.html"),
    js_api=api,
    width=1150, height=720,
)
# pywebview 6 的 start(func) 不再自动把 window 传给 func，要自己用 args 传
webview.start(probe, window)

print("=" * 64)
for k in sorted(result):
    print("%-18s = %r" % (k, result[k]))
print("=" * 64)

checks = [
    ("桥已注入", result.get("bridge") == "object"),
    ("connect 可调用", result.get("api_connect") == "function"),
    ("disconnect 可调用", result.get("api_disconnect") == "function"),
    ("就绪后按钮可点", result.get("btn_enabled") is True),
    ("初始状态文字", result.get("init_status") == "就绪"),
    # 不预填不是用户自己的主机/用户名，避免直接点「连接」连错地方
    ("主机默认留空", result.get("init_host") == ""),
    ("用户名默认留空", result.get("init_user") == ""),
    ("空主机被拦下", "请先填写主机地址" in str(result.get("blank_status"))),
    ("空主机：红色", result.get("blank_class") == "err"),
    ("空主机：按钮保持“连接”", result.get("blank_btn") == "连接"),
]
if HAVE_TARGET:
    checks += [
        ("错误：状态栏提示", "连接失败" in str(result.get("err_status"))),
        ("错误：红色", result.get("err_class") == "err"),
        ("错误：按钮保持“连接”", result.get("err_btn") == "连接"),
        ("错误：输入框未锁", result.get("err_locked") is False),
    ]
if HAVE_TARGET and PASSWORD:
    checks += [
        ("成功：状态栏 ok", result.get("ok_class") == "ok"),
        ("成功：按钮变“断开”", result.get("ok_btn") == "断开"),
        ("成功：输入框锁住", result.get("ok_locked") is True),
        ("断开：按钮变回“连接”", result.get("after_btn") == "连接"),
        ("断开：输入框解锁", result.get("after_locked") is False),
        ("重连：再次连上", result.get("re_class") == "ok"),
    ]

for name, good in checks:
    print("%-24s %s" % (name, "OK" if good else "FAIL"))

ok = all(good for _, good in checks)
print("=" * 64)
print("STEP2", "PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
