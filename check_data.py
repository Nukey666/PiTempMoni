# -*- coding: utf-8 -*-
"""真机验证：ECharts 出图 + 采集线程把数据推到前端。

需要一台能 SSH 的树莓派。连接信息一律走环境变量，不写进源码：

    set PI_HOST=192.168.1.100
    set PI_USER=pi
    set PI_PASS=你的密码
    python check_data.py
"""
import json
import os
import sys
import time

import webview

import app as appmod

result = {}

HOST = os.environ.get("PI_HOST", "")
USER = os.environ.get("PI_USER", "")
PASSWORD = os.environ.get("PI_PASS", "")

# 这个脚本整篇都依赖真机，三个变量缺一个就没法跑。
# 用 exit(2) 明确表示「跳过」而不是失败——clone 下来没树莓派的人不该看到一片红。
if not (HOST and USER and PASSWORD):
    print("跳过：请先设置 PI_HOST / PI_USER / PI_PASS 三个环境变量。")
    sys.exit(2)


def js(window, code):
    return window.evaluate_js(code)


# 往图上塞一段构造好的温度。__state 里存的就是那几个数组本身，改了就生效。
SET_TEMPS = """
(function (vals) {
  var st = window.__state;
  st.times.length = 0; st.temps.length = 0; st.rpms.length = 0;
  vals.forEach(function (t, i) {
    st.times.push('t' + i); st.temps.push(t); st.rpms.push(3000);
  });
  window.syncChart();
  return st.temps.length;
})(%s)
"""

# 数画布上有多少像素落在渐变两端：冷端 #58a6ff（冰蓝）、热端 #f85149（火红）。
# 这两个色在整张图上没有别的来源——轴名/刻度是琥珀和青，转速线是 #4ec9b0
# （跟冰蓝的蓝通道差 79，容差 40 分得开）。
# 注意红端有一条固定来源：85°C 的阈值虚线也是红的。所以冷/热两次要对比着看，
# 单看「有红」证明不了任何事——那条虚线一直都在。
COUNT_COLORS = """
(function () {
  var c = document.querySelector('#chart canvas');
  if (!c) return '0|0';
  var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  function near(i, tr, tg, tb) {
    return Math.abs(d[i] - tr) < 40 && Math.abs(d[i+1] - tg) < 40
        && Math.abs(d[i+2] - tb) < 40;
  }
  var blue = 0, red = 0;
  for (var i = 0; i < d.length; i += 4) {
    if (near(i, 88, 166, 255)) blue++;         // 冷端
    else if (near(i, 248, 81, 73)) red++;      // 热端
  }
  return blue + '|' + red;
})()
"""


def colors(key):
    """把 '琥珀数|红数' 解析成两个 int。解析不出来就回 (-1,-1)，
    让下面两条断言各自失败，而不是安静地当成 0 变成假通过。"""
    try:
        a, r = str(result.get(key, "")).split("|")
        return int(a), int(r)
    except (ValueError, TypeError):
        return -1, -1


def snap(window, prefix):
    result[prefix + "n"] = js(window, "window.__state.temps.length")
    result[prefix + "temps"] = js(window, "window.__state.temps.slice(-3)")
    result[prefix + "rpms"] = js(window, "window.__state.rpms.slice(-3)")
    result[prefix + "status"] = js(window, "document.getElementById('status').textContent")
    result[prefix + "readouts"] = js(
        window,
        "['v-temp','v-rpm','v-pwm'].map(function(i){return document.getElementById(i).textContent;}).join('|')")


def probe(window):
    time.sleep(2.5)
    result["echarts"] = js(window, "typeof echarts")
    result["series"] = js(window, "window.__state.chart.getOption().series.length")
    result["yaxes"] = js(window, "window.__state.chart.getOption().yAxis.length")
    result["positions"] = js(window, "JSON.stringify(window.__state.chart.getOption().yAxis.map(function(a){return [a.position, a.offset||0, a.min, a.max, a.interval||null];}))")

    js(window, """
      document.getElementById('host').value = %s;
      document.getElementById('user').value = %s;
      document.getElementById('pass').value = %s;
      document.getElementById('interval').value = '1';
      document.getElementById('btn').click();
    """ % (json.dumps(HOST), json.dumps(USER), json.dumps(PASSWORD)))

    time.sleep(6.5)                       # 1 秒间隔，应该攒到 4 个点以上
    snap(window, "run_")

    # PWM 不进图了（分档量，见 index.html 里 pwmPercent 的注释），但大号读数还得是
    # 百分比。pwmPercent 是顶层 function 声明，会挂到 window 上，可以直接拿真实档位验。
    # String(...) 要显式写：join 会把 null 悄悄变成空串，期望串里就再也对不上 null 了。
    result["pwm_map"] = js(window, """
      [0, 75, 125, 175, 250, 255, null]
        .map(function (v) { return String(window.pwmPercent(v)); })
        .join(',')
    """)
    result["pwm_shown"] = js(window, "document.getElementById('v-pwm').textContent")

    result["rendered"] = js(window, """
      (function () {
        var c = document.querySelector('#chart canvas');
        if (!c) return 'no canvas';
        var ctx = c.getContext('2d');
        var d = ctx.getImageData(0, 0, c.width, c.height).data;
        var colored = 0;
        for (var i = 0; i < d.length; i += 4) {
          // 统计明显是曲线颜色的像素（非深色背景）
          if (d[i] + d[i+1] + d[i+2] > 200) colored++;
        }
        return colored;
      })()
    """)

    js(window, "document.getElementById('btn').click();")   # 断开
    time.sleep(1.5)
    result["after_status"] = js(window, "document.getElementById('status').textContent")
    result["after_btn"] = js(window, "document.getElementById('btn').textContent")
    result["after_n"] = js(window, "window.__state.temps.length")

    # 规格：断开后曲线留着（清空是发生在“再次连接”时）。
    # 但要确认采集真的停了——再等 3 个采集周期，点数不许再涨。
    time.sleep(3.0)
    result["quiet_n"] = js(window, "window.__state.temps.length")

    # 再次连接：曲线必须从空开始重建
    js(window, "document.getElementById('btn').click();")
    time.sleep(1.2)
    result["recon_n"] = js(window, "window.__state.temps.length")

    # --- 温度渐变着色 + 85°C 阈值虚线 ---
    # 真机 idle 只有 48 度上下，颜色落在渐变中段，两端都够不到，
    # 所以这块只能在图上构造数据验渲染本身。
    js(window, "document.getElementById('btn').click();")   # 先断开，别让采集线程往数组里塞真数据
    time.sleep(1.5)

    result["vmap"] = js(window, """
      (function () {
        var v = window.__state.chart.getOption().visualMap;
        if (!v || !v.length) return 'none';
        v = v[0];
        return [v.type, v.min, v.max, v.show,
                ((v.inRange || {}).color || []).length].join('|');
      })()
    """)
    result["markline"] = js(window, """
      (function () {
        var ml = window.__state.chart.getOption().series[0].markLine;
        if (!ml || !ml.data || !ml.data.length) return 'none';
        return ml.data[0].yAxis + '|' + ml.lineStyle.type;
      })()
    """)

    for label, vals in (("cold_", [5, 8, 12, 15]), ("hot_", [92, 95, 98, 100])):
        js(window, SET_TEMPS % json.dumps(vals))
        time.sleep(0.8)                   # 等 ECharts 画完
        result[label + "colors"] = js(window, COUNT_COLORS)

    # --- 采样点数上限 ---
    # 直接灌 950 个样本，走的是真实的 onSample 路径。多出来的 50 个必须把**最老的**
    # 挤掉：结果 900 个点，最旧的是 50（0~49 被丢了）、最新的是 949。
    # 期望串写成 900|50|949 而不是只看个数——只数个数字的话，从新头裁剪也会通过。
    result["trim"] = js(window, """
      (function () {
        var st = window.__state;
        st.times.length = 0; st.temps.length = 0; st.rpms.length = 0;
        for (var i = 0; i < 950; i++) {
          window.onSample({temp: i, pwm: 0, rpm: 0});
        }
        return st.temps.length + '|' + st.temps[0] + '|'
             + st.temps[st.temps.length - 1];
      })()
    """)

    window.destroy()


api = appmod.Api()
window = webview.create_window(
    title="step3 check",
    url=os.path.join(appmod.BASE_DIR, "index.html"),
    js_api=api, width=1150, height=720,
)
webview.start(probe, window)

print("=" * 66)
for k in sorted(result):
    print("%-16s = %r" % (k, result[k]))
print("=" * 66)

checks = [
    ("ECharts 已加载", result.get("echarts") in ("object", "function")),
    ("两条 series", result.get("series") == 2),
    ("两根 Y 轴", result.get("yaxes") == 2),
    ("采样点 >= 3", isinstance(result.get("run_n"), int) and result.get("run_n") >= 3),
    ("温度有值", bool(result.get("run_temps")) and result["run_temps"][-1] is not None),
    ("转速有值", bool(result.get("run_rpms")) and result["run_rpms"][-1] is not None),
    ("状态栏显示状态", "采集中" in str(result.get("run_status"))),
    ("大号读数有值", all(v not in ("", "—") for v in str(result.get("run_readouts")).split("|"))),
    ("温度轴 0~100 每 10 一格", '"left",0,0,100,10' in str(result.get("positions"))),
    ("转速轴 0~10000", '"right",0,0,10000,null' in str(result.get("positions"))),
    # PWM 是分档量，不进图也不配专属坐标轴；只保留大号读数
    ("PWM 不进图", result.get("series") == 2 and result.get("yaxes") == 2),
    # 五个真实档位 [0,75,125,175,250] + 满量程 255 + 空值，全部归到十位。
    ("PWM 归到十位", result.get("pwm_map") == "0,30,50,70,100,100,null"),
    ("PWM 读数在 0~100", str(result.get("pwm_shown", "")).isdigit()
     and 0 <= int(result.get("pwm_shown", "999")) <= 100),
    ("画面确实画了东西", isinstance(result.get("rendered"), int) and result["rendered"] > 1000),
    # 温度渐变：0°C 冰蓝 → 100°C 火红，min/max 必须跟温度轴一致
    # 注意是 JS 的 false（小写），不是 Python 的 False——这个串是 JS join 出来的
    ("渐变是连续型", result.get("vmap") == "continuous|0|100|false|5"),
    ("85°C 阈值虚线", result.get("markline") == "85|dashed"),
    # 这两条也要成对看：低温端是蓝、高温端不是蓝，才说明颜色真的跟着温度走
    ("低温段是冰蓝", colors("cold_colors")[0] > 0),
    ("高温段不是冰蓝", colors("hot_colors")[0] == 0),
    # 红端有阈值虚线这个固定来源，所以只能跟低温时比：多出来的才是曲线本身
    ("高温段红色远超阈值线", colors("hot_colors")[1] > colors("cold_colors")[1] * 2),
    # 900 = 默认间隔（2s）下的 30 分钟；丢的是最老的，不是最新的
    ("采样点上限 900 且丢老留新", result.get("trim") == "900|50|949"),
    ("断开后按钮复位", result.get("after_btn") == "连接"),
    ("断开后曲线保留", isinstance(result.get("after_n"), int) and result["after_n"] >= 3),
    ("断开后立即停采", result.get("quiet_n") == result.get("after_n")),
    ("重连时清空曲线", isinstance(result.get("recon_n"), int)
     and result["recon_n"] < result["after_n"] and result["recon_n"] <= 2),
]
for name, good in checks:
    print("%-22s %s" % (name, "OK" if good else "FAIL"))
ok = all(g for _, g in checks)
print("=" * 66)
print("STEP3", "PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
