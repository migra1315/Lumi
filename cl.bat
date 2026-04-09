@echo off
:: 1. 切换控制台编码为 UTF-8，确保中文显示正常
chcp 65001 >nul

:: 2. 设置局部环境变量 (仅对当前进程及其子进程有效)
:: 请确保 1080 与你的 Shadowrocket 本地代理端口一致
set HTTP_PROXY=http://127.0.0.1:33210
set HTTPS_PROXY=http://127.0.0.1:33210

echo [Claude CLI] 代理隧道已建立 (Port:33210)...

:: 3. 动态寻找并执行原始的 claude 程序
:: %* 允许你传递所有参数，如 cl /doctor 或 cl "explain this code"
for /f "delims=" %%i in ('where claude') do (
    "%%i" %*
    goto :finish
)

:finish
:: 脚本结束，变量自动销毁，不影响系统其他程序