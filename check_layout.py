# -*- coding: utf-8 -*-
"""布局检查：各窗口尺寸下不溢出、标签不折行、按钮不被挤出。

v1（tkinter）就栽在这里——写死的最小宽度把“停止”按钮挤出了窗口。
"""
import os
import sys
import time

import webview

import app as appmod

MEASURE = """
(function () {
  var de = document.documentElement;
  var bar = document.querySelector('.bar');
  var labels = [].slice.call(document.querySelectorAll('.bar label'));
  var btn = document.getElementById('btn').getBoundingClientRect();
  var chart = document.getElementById('chart');
  return {
    viewW: de.clientWidth,
    viewH: de.clientHeight,
    overflowX: de.scrollWidth - de.clientWidth,
    overflowY: de.scrollHeight - de.clientHeight,
    barOverflow: bar.scrollWidth - bar.clientWidth,
    labelH: Math.max.apply(null, labels.map(function (l) {
      return Math.round(l.getBoundingClientRect().height);
    })),
    btnRight: Math.round(btn.right),
    btnVisible: btn.width > 0 && btn.right <= de.clientWidth + 1,
    chartW: chart.clientWidth,
    chartH: chart.clientHeight,
    readoutH: Math.round(document.querySelector('.readouts').getBoundingClientRect().height),
    children: [].slice.call(bar.children).map(function (el) {
      var cs = getComputedStyle(el);
      return (el.id || el.className || el.tagName.toLowerCase()) +
             ' w=' + Math.round(el.getBoundingClientRect().width) +
             ' shrink=' + cs.flexShrink + ' min=' + cs.minWidth;
    })
  };
})()
"""

rows = []


def probe(window):
    time.sleep(2.5)

    for label, (w, h) in (
        ("默认 1150x720", (1150, 720)),
        ("最小 900x560", (900, 560)),
        ("更小 700x500", (700, 500)),      # 应被 min_size 挡回 900x560
        ("拉宽 1400x900", (1400, 900)),
        ("回默认", (1150, 720)),
    ):
        window.resize(w, h)
        time.sleep(1.2)
        m = window.evaluate_js(MEASURE)
        m["label"] = label
        m["asked"] = "%dx%d" % (w, h)
        rows.append(m)

    window.destroy()


api = appmod.Api()
window = webview.create_window(
    title="layout check",
    url=os.path.join(appmod.BASE_DIR, "index.html"),
    js_api=api, width=1150, height=720, min_size=(900, 560),
)
webview.start(probe, window)

print("=" * 92)
print("%-16s %-11s %-11s %7s %7s %7s %7s %6s %7s" % (
    "尺寸", "请求", "实际", "溢出X", "溢出Y", "栏溢出", "标签高", "按钮可见", "图高"))
print("-" * 92)
for m in rows:
    print("%-16s %-11s %-11s %7d %7d %7d %7d %8s %7d" % (
        m["label"], m["asked"], "%dx%d" % (m["viewW"], m["viewH"]),
        m["overflowX"], m["overflowY"], m["barOverflow"], m["labelH"],
        m["btnVisible"], m["chartH"]))
print("=" * 92)

bad = []
for m in rows:
    where = "%s (%dx%d)" % (m["label"], m["viewW"], m["viewH"])
    if m["overflowX"] > 0:
        bad.append("%s 横向溢出 %dpx" % (where, m["overflowX"]))
    if m["overflowY"] > 0:
        bad.append("%s 纵向溢出 %dpx" % (where, m["overflowY"]))
    if m["labelH"] > 26:
        bad.append("%s 标签折行了（高 %dpx）" % (where, m["labelH"]))
    if not m["btnVisible"]:
        bad.append("%s 按钮被挤出窗口（右缘 %d > 宽 %d）" % (where, m["btnRight"], m["viewW"]))
    if m["chartH"] < 180:
        bad.append("%s 图表太矮（%dpx）" % (where, m["chartH"]))

# min_size 该挡住更小的请求：请求 700x500 应与请求 900x560 得到同样大小。
# （window.resize 走的是物理像素，viewW/viewH 是 CSS 像素，两者数值不能直接比。）
small, tiny = rows[1], rows[2]
if (tiny["viewW"], tiny["viewH"]) != (small["viewW"], small["viewH"]):
    bad.append("min_size 没兜住：请求 700x500 得 %dx%d，请求 900x560 得 %dx%d"
               % (tiny["viewW"], tiny["viewH"], small["viewW"], small["viewH"]))

print("\n最小尺寸下控制栏各子元素实宽：")
for line in rows[1].get("children", []):
    print("   ", line)

for b in bad:
    print("FAIL:", b)
print("LAYOUT", "PASSED" if not bad else "FAILED")
sys.exit(0 if not bad else 1)
