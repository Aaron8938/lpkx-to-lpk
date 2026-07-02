@echo off
setlocal enabledelayedexpansion

rem ============================================================
rem  lpkx 转 lpk 工具启动器
rem  - 支持拖入单个/多个 .lpkx 文件
rem  - 支持拖入文件夹(自动查找 .lpkx)
rem  - 支持手动输入路径
rem  - 输出 .lpk 到 lpkx 同目录下
rem ============================================================

rem ---- 配置区 ----
set "PRO_PYTHON=D:\arcpy_env\python.exe"
set "ARCMAP_PYTHON=C:\Python27\ArcGIS10.8\python.exe"
set "SCRIPT_DIR=%~dp0"
set "EXTRACT_SCRIPT=%SCRIPT_DIR%lpkx_to_lpk_extract.py"
set "BUILD_SCRIPT=%SCRIPT_DIR%lpkx_to_lpk_build.py"
set "TEMP_DIR=%TEMP%\lpkx_to_lpk"
set "PYTHONIOENCODING=gbk"

rem ---- 强制 GBK 代码页，确保 bat 与 Python 中文输出一致 ----
chcp 936 >nul 2>&1

rem ---- 标题 ----
title lpkx 转 lpk 工具
echo ============================================================
echo   lpkx 转 lpk 工具 (保留原 lpkx 样式)
echo   ArcGIS Pro -^> ArcMap 图层包转换
echo ============================================================
echo.

rem ---- 检查环境 ----
if not exist "%PRO_PYTHON%" (
    echo [错误] 未找到 Pro Python: %PRO_PYTHON%
    echo        请确认 D:\arcpy_env 是 ArcGIS Pro 的克隆环境
    pause
    exit /b 1
)
if not exist "%ARCMAP_PYTHON%" (
    echo [错误] 未找到 ArcMap Python: %ARCMAP_PYTHON%
    echo        请确认已安装 ArcMap 10.8
    pause
    exit /b 1
)
if not exist "%EXTRACT_SCRIPT%" (
    echo [错误] 未找到 Pro 端脚本: %EXTRACT_SCRIPT%
    pause
    exit /b 1
)
if not exist "%BUILD_SCRIPT%" (
    echo [错误] 未找到 ArcMap 端脚本: %BUILD_SCRIPT%
    pause
    exit /b 1
)

rem ---- 创建临时目录 ----
if not exist "%TEMP_DIR%" mkdir "%TEMP_DIR%"

rem ---- 收集待处理文件列表 ----
set "FILE_LIST=%TEMP_DIR%\file_list.txt"
if exist "%FILE_LIST%" del "%FILE_LIST%"

if "%~1"=="" goto interactive_mode
goto parse_args

:interactive_mode
set "input="
set /p "input=请输入或拖入 .lpkx 文件路径: "
rem 去除可能的引号
set "input=!input:"=!"
if "!input!"=="" (
    echo.
    echo 未输入路径，退出。
    pause
    exit /b 0
)
if exist "!input!\*" goto input_is_folder
if exist "!input!" goto input_is_file
echo   [错误] 路径不存在: !input!
pause
exit /b 0

:input_is_folder
echo   扫描文件夹: !input!
for /r "!input!" %%f in (*.lpkx) do (
    echo %%f>>"%FILE_LIST%"
)
goto collect_done

:input_is_file
echo !input!>>"%FILE_LIST%"
goto collect_done

:parse_args
if "%~1"=="" goto collect_done
if exist "%~1\*" goto arg_is_folder
if exist "%~1" goto arg_is_file
echo   [跳过] 路径无效: %~1
shift
goto parse_args

:arg_is_folder
echo   扫描文件夹: %~1
for /r "%~1" %%f in (*.lpkx) do (
    echo %%f>>"%FILE_LIST%"
)
shift
goto parse_args

:arg_is_file
echo %~1>>"%FILE_LIST%"
shift
goto parse_args

:collect_done
rem ---- 检查是否有文件要处理 ----
if not exist "%FILE_LIST%" (
    echo.
    echo 没有需要处理的文件。
    pause
    exit /b 0
)

rem 统计文件数
set /a total=0
for /f "usebackq delims=" %%a in ("%FILE_LIST%") do set /a total+=1

if %total%==0 (
    echo.
    echo 没有找到 .lpkx 文件。
    pause
    exit /b 0
)

echo.
echo 共发现 %total% 个文件待处理
echo ============================================================
echo.

rem ---- 报告文件 ----
set "REPORT=%TEMP_DIR%\report_%random%.txt"
echo ============================================================ > "%REPORT%"
echo   lpkx 转 lpk 处理报告 >> "%REPORT%"
echo   时间: %date% %time% >> "%REPORT%"
echo   共 %total% 个文件 >> "%REPORT%"
echo ============================================================ >> "%REPORT%"
echo. >> "%REPORT%"

rem ---- 逐个处理 ----
set /a success=0
set /a failed=0
set /a idx=0

for /f "usebackq delims=" %%F in ("%FILE_LIST%") do (
    set /a idx+=1
    set "lpkx_path=%%F"
    set "lpkx_name=%%~nxF"
    set "lpkx_dir=%%~dpF"
    if "!lpkx_dir:~-1!"=="\" set "lpkx_dir=!lpkx_dir:~0,-1!"
    set "out_name=!lpkx_name:~0,-5!.lpk"
    set "out_lpk=!lpkx_dir!\!out_name!"

    echo ----------------------------------------------------------------
    echo [!idx!/%total%] !lpkx_name!
    echo ----------------------------------------------------------------

    set "json_path=%TEMP_DIR%\mid_!idx!_%random%.json"

    echo   [1/2] Pro端: 解包并提取样式...
    "%PRO_PYTHON%" "%EXTRACT_SCRIPT%" "!lpkx_path!" "!json_path!"
    if not exist "!json_path!" (
        echo   [失败] Pro端处理失败
        echo [!idx!] !lpkx_name! - FAILED ^(Pro extract^) >> "%REPORT%"
        set /a failed+=1
        echo.
        goto :next_file
    )

    echo   [2/2] ArcMap端: 重建并打包 lpk...
    "%ARCMAP_PYTHON%" "%BUILD_SCRIPT%" "!json_path!" "!out_lpk!"
    if exist "!out_lpk!" (
        echo   [成功] 输出: !out_lpk!
        echo [!idx!] !lpkx_name! - OK - !out_name! >> "%REPORT%"
        set /a success+=1
    ) else (
        echo   [失败] 输出文件未生成
        echo [!idx!] !lpkx_name! - FAILED ^(no output^) >> "%REPORT%"
        set /a failed+=1
    )

    if exist "!json_path!" del "!json_path!" 2>nul

    :next_file
    echo.
)

rem ---- 汇总报告 ----
echo ============================================================
echo   处理完成
echo ============================================================
echo   成功: %success% / %total%
echo   失败: %failed% / %total%
echo.

echo. >> "%REPORT%"
echo ============================================================ >> "%REPORT%"
echo   汇总: 成功 %success% / 失败 %failed% / 总计 %total% >> "%REPORT%"
echo ============================================================ >> "%REPORT%"

echo 详细报告已保存: %REPORT%
echo.
type "%REPORT%"

echo.
echo 按任意键退出...
pause >nul
endlocal
