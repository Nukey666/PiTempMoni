@echo off
chcp 65001 >nul
rem 一键打包：生成 dist\PiMonitor.exe
setlocal

echo [1/3] 安装依赖 ...
python -m pip install -r requirements.txt || goto :err

echo [2/3] 清理旧产物 ...
if exist build rmdir /s /q build

echo [3/3] 打包 ...
rem index.html 和 echarts.min.js 必须一起打进去：
rem 页面用 html= 内联方式加载，运行时要读到这两个文件。
python -m PyInstaller --onefile --windowed --name PiMonitor ^
    --add-data "index.html;." --add-data "echarts.min.js;." ^
    --noconfirm app.py || goto :err

echo.
echo 完成：dist\PiMonitor.exe
echo 该 exe 可直接拷贝到任意 Windows 机器双击运行，无需安装 Python，也不需要联网。
goto :eof

:err
echo.
echo 打包失败，请检查上面的错误信息。
exit /b 1
