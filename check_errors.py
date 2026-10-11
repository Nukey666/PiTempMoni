# -*- coding: utf-8 -*-
"""错误路径验证：采集到一半断网 / 主机不存在 / 远程回空 / 断开逻辑。

假 SSH 服务端先正常回两条数据，然后直接掐掉 transport 模拟断网。
不需要真树莓派，也不需要 bash。

场景 D 是 v1 老坑的回归测试：远程命令每个字段都回空时，界面必须报错停机，
不能一直挂着绿色的“采集中”当没事发生。
"""
import json
import os
import socket
import sys
import threading
import time

import paramiko
import webview

import app as appmod

result = {}

SAMPLE_LINE = b"55.5,125,5000\n"
SAMPLES_BEFORE_DROP = 2

# 远程命令整体失败时（thermal zone 路径变了 / awk 不在），printf 出来的就是
# 这么一个“三个字段全空”的行。它和“某一轮恰好没读到”在解析结果上完全一样，
# 界面曾经会一直挂着绿色的“采集中”、图永远空着——这是 v1 带过来的老坑。
EMPTY_LINE = b",,\n"


class FakeServer(paramiko.ServerInterface):
    def __init__(self, line=SAMPLE_LINE, drop_after=SAMPLES_BEFORE_DROP):
        self.line = line
        self.drop_after = drop_after      # None = 永远不断
        self.count = 0
        self.transport = None

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        self.count += 1
        if self.drop_after is not None and self.count > self.drop_after:
            # 模拟断网：把整条连接掐了
            if self.transport is not None:
                self.transport.close()
            return False
        threading.Thread(target=self._serve, args=(channel,), daemon=True).start()
        return True

    def _serve(self, channel):
        channel.sendall(self.line)
        channel.send_exit_status(0)
        time.sleep(0.05)
        channel.close()


class Server(object):
    def __init__(self, line=SAMPLE_LINE, drop_after=SAMPLES_BEFORE_DROP):
        self.line = line
        self.drop_after = drop_after
        self.key = paramiko.RSAKey.generate(2048)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.servers = []
        self.stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stop:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client):
        tr = paramiko.Transport(client)
        tr.add_server_key(self.key)
        srv = FakeServer(self.line, self.drop_after)
        srv.transport = tr
        self.servers.append(srv)
        try:
            tr.start_server(server=srv)
        except Exception:
            return
        while tr.is_active():
            time.sleep(0.05)

    def close(self):
        self.stop = True
        try:
            self.sock.close()
        except Exception:
            pass


srv = Server()                                        # 正常回两条，然后断网
srv_empty = Server(EMPTY_LINE, drop_after=None)       # 一直回“三个字段全空”


def js(window, code):
    return window.evaluate_js(code)


def snap(window, prefix):
    result[prefix + "n"] = js(window, "window.__state.temps.length")
    result[prefix + "vals"] = js(window, "window.__state.temps.slice(-2)")
    result[prefix + "status"] = js(window, "document.getElementById('status').textContent")
    result[prefix + "class"] = js(window, "document.getElementById('status').className")
    result[prefix + "btn"] = js(window, "document.getElementById('btn').textContent")
    result[prefix + "locked"] = js(window, "document.getElementById('host').disabled")


def probe(window):
    time.sleep(2.5)

    # --- 场景 A：主机不存在（连接阶段就失败）---
    js(window, """
      document.getElementById('host').value = '127.0.0.1';
      document.getElementById('port').value = '1';
      document.getElementById('user').value = 'x';
      document.getElementById('pass').value = 'y';
      document.getElementById('btn').click();
    """)
    time.sleep(3.0)
    snap(window, "badhost_")

    # --- 场景 B：连上后采集中途断网 ---
    js(window, """
      document.getElementById('host').value = '127.0.0.1';
      document.getElementById('port').value = '%d';
      document.getElementById('interval').value = '1';
      document.getElementById('btn').click();
    """ % srv.port)
    time.sleep(6.0)
    snap(window, "drop_")

    # --- 场景 D：连上了，但远程命令每个字段都回空 ---
    # 这是 v1 带过来的老坑：解析出来三个 None，界面当成“正常一轮”照画，
    # 于是永远绿色的“采集中”、图永远空着，用户看到的是卡死不是错误。
    js(window, """
      document.getElementById('host').value = '127.0.0.1';
      document.getElementById('port').value = '%d';
      document.getElementById('user').value = 'x';
      document.getElementById('pass').value = 'y';
      document.getElementById('interval').value = '1';
      document.getElementById('btn').click();
    """ % srv_empty.port)
    time.sleep(6.0)
    snap(window, "empty_")
    result["empty_raw"] = js(window, "window.__state.temps.slice(-3)")

    # --- 场景 C：出错之后还能重新连（服务端已掐，这次仍会失败，但不能崩）---
    result["still_alive"] = js(window, "1 + 1")

    window.destroy()


api = appmod.Api()
window = webview.create_window(
    title="step4 check",
    url=os.path.join(appmod.BASE_DIR, "index.html"),
    js_api=api, width=1150, height=720,
)
webview.start(probe, window)
srv.close()
srv_empty.close()

print("=" * 68)
for k in sorted(result):
    print("%-16s = %r" % (k, result[k]))
print("=" * 68)

checks = [
    ("主机不可达：报错", "连接失败" in str(result.get("badhost_status"))),
    ("主机不可达：红色", result.get("badhost_class") == "err"),
    ("主机不可达：按钮复位", result.get("badhost_btn") == "连接"),
    ("主机不可达：输入框解锁", result.get("badhost_locked") is False),
    ("断网前采到了数据", isinstance(result.get("drop_n"), int) and result["drop_n"] >= 1),
    ("断网：状态栏报错", "采集异常" in str(result.get("drop_status"))),
    ("断网：红色", result.get("drop_class") == "err"),
    ("断网：按钮复位", result.get("drop_btn") == "连接"),
    ("断网：输入框解锁", result.get("drop_locked") is False),
    # 场景 D：远程一直回 ",," —— 必须报错停机，不能静默卡死
    ("空输出：状态栏报错", "读不到温度" in str(result.get("empty_status"))),
    ("空输出：红色", result.get("empty_class") == "err"),
    ("空输出：按钮复位", result.get("empty_btn") == "连接"),
    ("空输出：输入框解锁", result.get("empty_locked") is False),
    ("程序没崩", result.get("still_alive") == 2),
]
for name, good in checks:
    print("%-22s %s" % (name, "OK" if good else "FAIL"))
ok = all(g for _, g in checks)
print("=" * 68)
print("STEP4", "PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
