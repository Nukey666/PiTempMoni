# -*- coding: utf-8 -*-
"""树莓派监控 —— pywebview 桌面壳（第 2 步：先只打通窗口与 JS<->Python 桥）。

没有 HTTP 服务器、不监听任何端口：前端 JS 直接调这里的 Api 方法，
Python 通过 window.evaluate_js() 直接回调页面里的全局函数。
"""
import json
import os
import sys
import threading
import time

import webview

from reader import SSHReader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_PORT = 22
DEFAULT_INTERVAL = 2.0
MIN_INTERVAL = 0.5
SLEEP_SLICE = 0.05        # 间隔等待的检查粒度（秒），保证“断开”能立刻生效
MAX_TEMP_MISSES = 3       # 连续几次读不到温度就报错停机（温度是必选项，见 §9）

state = {
    "reader": None,
    "running": False,
    "interval": DEFAULT_INTERVAL,
}


def _exc_text(exc):
    """异常转成人看的文字。

    有些异常（连接被掐断时最常见）str() 是空字符串，
    直接拼进状态栏就成了“采集异常: ”这么一句没头没尾的话。
    """
    text = str(exc).strip()
    name = type(exc).__name__
    return "%s: %s" % (name, text) if text else name


def _stall_msg(misses, data):
    """连续读不到温度时的报错文案。

    把远程原样回了什么带上——「全是空」和「压根没输出」的排查方向完全不同，
    光说一句“读不到温度”等于没说。
    """
    raw = (data.get("raw") or "").strip()
    return "连续 %d 次读不到温度，已停止。远程返回: %s" % (
        misses, raw if raw else "(空 —— 命令没有任何输出)")


class Api(object):
    """pywebview 把这里的公开方法挂到 window.pywebview.api 上供 JS 调用。"""

    def __init__(self):
        # 必须用下划线开头的私有属性存窗口句柄。
        # pywebview 会枚举 js_api 对象的公开成员来搭 JS 桥；一旦碰到
        # webview.Window 对象，它就顺着 window.native.<属性>.<属性>... 一路递归
        # （AccessibilityObject.Bounds.Empty.Empty.Empty...），最后
        # "Main window failed to start"——开发时不一定发作，打包后必炸。
        self._win = None

    def _attach(self, window):
        """注入窗口句柄（私有方法，同样不会进 JS 桥）。"""
        self._win = window

    # ------------------------------------------------------------------
    # 推数据给前端。页面里的 onXxx 是全局函数，可能还没定义，
    # 所以统一写成 `window.onXxx && window.onXxx(...)` 的形式。
    # ------------------------------------------------------------------
    def _get_window(self):
        """拿到窗口句柄。没注入就退回 webview.windows，取不到要吭声。"""
        if self._win is not None:
            return self._win
        try:
            return webview.windows[0]
        except IndexError:
            print("没有可用窗口，前端回调发不出去", file=sys.stderr)
            return None

    def _call_js(self, func, payload):
        window = self._get_window()
        if window is None:
            return
        code = "window.%s && window.%s(%s)" % (func, func, json.dumps(payload))
        try:
            window.evaluate_js(code)
        except Exception as exc:
            # 窗口关掉之后回调必然失败，不该把采集线程带崩
            print("evaluate_js 失败: %s" % exc, file=sys.stderr)

    def connect(self, host, port, user, password, interval):
        """JS 调用：连上树莓派并开始采集。返回值一定是 {'ok': bool, 'msg': str}。"""
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        try:
            interval = max(MIN_INTERVAL, float(interval))
        except (TypeError, ValueError):
            interval = DEFAULT_INTERVAL

        # 主机和用户名现在默认留空（页面不再预填 192.168.1.100 / pi）。
        # 空用户名丢给 paramiko 会抛一句很难懂的异常，这里先拦成一句人话。
        if not host:
            return {"ok": False, "msg": "请先填写主机地址"}
        if not user:
            return {"ok": False, "msg": "请先填写用户名"}

        if state["reader"] is not None:
            return {"ok": False, "msg": "已经在连接中"}

        try:
            reader = SSHReader(host, port, user, password)
        except Exception as exc:
            return {"ok": False, "msg": "连接失败: %s" % _exc_text(exc)}

        state["reader"] = reader
        state["running"] = True
        state["interval"] = interval
        threading.Thread(target=self._loop, args=(reader, interval), daemon=True).start()
        return {"ok": True, "msg": "已连接 %s" % host}

    def disconnect(self):
        """JS 调用：断开并停止采集。"""
        state["running"] = False
        reader, state["reader"] = state["reader"], None
        if reader is not None:
            reader.close()
        return {"ok": True, "msg": "已断开"}

    # ------------------------------------------------------------------
    # 采集线程
    # ------------------------------------------------------------------
    def _alive(self, reader):
        """这轮采集是否还算数：没被断开、且没被新的连接顶掉。"""
        return state["running"] and state["reader"] is reader

    def _loop(self, reader, interval):
        misses = 0                          # 连续读不到温度的轮数
        try:
            while self._alive(reader):
                t0 = time.monotonic()
                try:
                    data = reader.read_once()
                except Exception as exc:
                    if self._alive(reader):
                        state["running"] = False
                        state["reader"] = None
                        reader.close()          # 断了也要把连接关掉，别漏 fd
                        self._call_js("onError", "采集异常: %s" % _exc_text(exc))
                    return

                if data.get("temp") is None:
                    misses += 1
                    # 温度是必选项。远程命令整体失败（路径变了、awk 不在、shell
                    # 没起来）时三个字段全是空，解析结果和「这一轮恰好没读到」
                    # 长得一模一样，界面会一直显示绿色的"采集中"、图永远空着——
                    # 用户看到的是卡死，不是错误。连续几轮都拿不到就主动报错停机，
                    # 并把远程原样回了什么带出来，否则没法查。
                    if misses >= MAX_TEMP_MISSES:
                        state["running"] = False
                        state["reader"] = None
                        reader.close()
                        self._call_js("onError", _stall_msg(misses, data))
                        return
                else:
                    misses = 0

                self._call_js("onSample", data)

                # 补足到设定间隔，但每 50ms 醒一次，断开时能立刻退出
                while self._alive(reader):
                    remain = t0 + interval - time.monotonic()
                    if remain <= 0:
                        break
                    time.sleep(min(SLEEP_SLICE, remain))
        finally:
            if state["reader"] is None:      # 真的是结束了，而不是被新连接顶掉
                self._call_js("onStopped", None)


ECHARTS_TAG = '<script src="echarts.min.js"></script>'


def _page_html():
    """读 index.html，并把 echarts.min.js 内联进去，返回完整页面。

    为什么不用 url= 指向本地文件：pywebview 的 is_local_url() 把"文件路径"
    判为本地资源，会专门起一个本地 HTTP 服务来喂页面（webview/__init__.py
    里的 `http_server or has_local_urls`，所以 http_server=False 也拦不住），
    进程就会监听 127.0.0.1 上的随机端口——违反规格"不监听任何端口"。
    改用 html= 时 original_url 为 None，判定为非本地，完全不经过任何服务。
    代价是页面里的外部脚本得内联，反正这个应用本来就该能离线跑。

    （试过 file:/// 形式：is_local_url() 会放行，但 WebView2 静默不加载，白屏。）
    """
    with open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8") as fh:
        page = fh.read()
    if ECHARTS_TAG not in page:
        raise RuntimeError(
            "index.html 里找不到 %s，无法内联 ECharts" % ECHARTS_TAG)
    with open(os.path.join(BASE_DIR, "echarts.min.js"), encoding="utf-8") as fh:
        echarts_js = fh.read()
    return page.replace(ECHARTS_TAG, "<script>\n%s\n</script>" % echarts_js)


def main():
    api = Api()
    window = webview.create_window(
        title="树莓派温度 / 风扇监控",
        html=_page_html(),
        js_api=api,
        width=1150,
        height=720,
        min_size=(900, 560),
    )
    api._attach(window)
    webview.start(http_server=False)   # 双保险：页面已内联，本就不需要服务


if __name__ == "__main__":
    sys.exit(main())
