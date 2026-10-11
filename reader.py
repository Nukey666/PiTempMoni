# -*- coding: utf-8 -*-
"""SSH 采集模块：只负责“连上去、读一次、断开”。

刻意不依赖 GUI / pywebview，可以单独 import 到任何脚本里用。

命令行自测（密码走环境变量，不写进源码）：

    set PI_HOST=192.168.1.100
    set PI_USER=pi
    set PI_PASS=你的密码
    python reader.py
"""
import os
import sys

import paramiko

# 一次 SSH 调用拿回三个值：温度(°C)、PWM 原始值(0~255)、转速(RPM)
# 任一项读不到就留空，由客户端解析成 None，不让整条命令失败。
REMOTE_CMD = r"""
T=$(awk '{printf "%.1f", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
F=""
for d in /sys/class/hwmon/hwmon*; do
  n=$(cat "$d/name" 2>/dev/null)
  if [ "$n" = "pwmfan" ] || [ "$n" = "pwm-fan" ]; then F="$d"; break; fi
done
P=$(cat "$F/pwm1" 2>/dev/null)
R=$(cat "$F/fan1_input" 2>/dev/null)
printf "%s,%s,%s\n" "$T" "$P" "$R"
"""

CONNECT_TIMEOUT = 8       # 建立连接的超时（秒）
COMMAND_TIMEOUT = 5       # 单次命令的超时（秒）
RAW_KEEP = 160            # 原始输出回传时最多留多少个字符（只用于报错，不该无限长）


def _to_float(text):
    """能转就转，转不了返回 None —— 一个字段格式不对不该让整轮采集崩掉。"""
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _to_int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


class SSHReader(object):
    """一条 SSH 连接，反复 read_once()。"""

    def __init__(self, host, port, user, password):
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(host, port=port, username=user,
                         password=password, timeout=CONNECT_TIMEOUT)

    def read_once(self):
        """读一次，返回 {'temp':float|None, 'pwm':int|None, 'rpm':int|None, 'raw':str}。

        连接断了 / 命令超时会抛异常，由调用方决定是重连还是报错。

        `raw` 是远程命令的原始输出行（截断过的）。三个字段全空、和「命令压根
        没输出」在解析结果上是一样的（都是 None），但原因完全不同——前者多半是
        thermal zone 路径变了或 awk 不在，后者多半是 shell 没起来。调用方要靠
        这个字段才能报出一句能查的话，所以留着。
        """
        _, out, _ = self.ssh.exec_command(REMOTE_CMD, timeout=COMMAND_TIMEOUT)
        text = out.read().decode("utf-8", "ignore")
        line = ""
        for raw in text.splitlines():
            if raw.strip():
                line = raw.strip()
                break

        parts = line.split(",")
        while len(parts) < 3:
            parts.append("")

        t = parts[0].strip()
        p = parts[1].strip()
        r = parts[2].strip()
        return {
            "temp": _to_float(t),
            "pwm": _to_int(p),
            "rpm": _to_int(r),
            "raw": line[:RAW_KEEP],
        }

    def close(self):
        try:
            self.ssh.close()
        except Exception:
            pass


def _main():
    host = os.environ.get("PI_HOST", "192.168.1.100")
    user = os.environ.get("PI_USER", "pi")
    password = os.environ.get("PI_PASS", "")
    try:
        port = int(os.environ.get("PI_PORT", "22"))
    except ValueError:
        port = 22

    if not password:
        print("请先设置环境变量 PI_PASS（密码不要写进源码）")
        return 1

    print("连接 %s@%s:%d ..." % (user, host, port))
    try:
        reader = SSHReader(host, port, user, password)
    except Exception as exc:
        # 只有命令行入口把它变成人话；import 本模块的调用方拿到的仍是原始异常
        print("连接失败: %s: %s" % (type(exc).__name__, exc))
        return 1

    try:
        for i in range(3):
            print(reader.read_once())
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
