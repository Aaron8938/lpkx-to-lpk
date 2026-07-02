# -*- coding: utf-8 -*-
"""
run_launcher.py
===============
lpkx 转 lpk 工具 - Python 启动器
功能与 bat 相同，但编码更可靠。支持:
  - 拖入/输入单个或多个 .lpkx 文件路径
  - 输入文件夹路径（自动查找 .lpkx）
  - 输出 .lpk 到 lpkx 同目录下

用法:
  1. 双击运行（用 Pro 的 Python）
  2. 或命令行: python run_launcher.py [文件1] [文件2] [文件夹]
"""
import os
import sys
import subprocess
import tempfile
import time

# ---- 配置 ----
PRO_PYTHON = r"D:\arcpy_env\python.exe"
ARCMAP_PYTHON = r"C:\Python27\ArcGIS10.8\python.exe"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACT_SCRIPT = os.path.join(SCRIPT_DIR, "lpkx_to_lpk_extract.py")
BUILD_SCRIPT = os.path.join(SCRIPT_DIR, "lpkx_to_lpk_build.py")
TEMP_DIR = os.path.join(tempfile.gettempdir(), "lpkx_to_lpk")


def check_env():
    """检查环境"""
    errors = []
    if not os.path.exists(PRO_PYTHON):
        errors.append("未找到 Pro Python: %s" % PRO_PYTHON)
    if not os.path.exists(ARCMAP_PYTHON):
        errors.append("未找到 ArcMap Python: %s" % ARCMAP_PYTHON)
    if not os.path.exists(EXTRACT_SCRIPT):
        errors.append("未找到 Pro 端脚本: %s" % EXTRACT_SCRIPT)
    if not os.path.exists(BUILD_SCRIPT):
        errors.append("未找到 ArcMap 端脚本: %s" % BUILD_SCRIPT)
    return errors


def collect_files(args):
    """从参数收集 lpkx 文件列表"""
    files = []
    for arg in args:
        arg = arg.strip().strip('"').strip("'")
        if not arg:
            continue
        if os.path.isdir(arg):
            # 文件夹 → 递归查找 lpkx
            print("  扫描文件夹: %s" % arg)
            for root, dirs, fnames in os.walk(arg):
                for f in fnames:
                    if f.lower().endswith(".lpkx"):
                        files.append(os.path.join(root, f))
        elif os.path.isfile(arg) and arg.lower().endswith(".lpkx"):
            files.append(arg)
        else:
            print("  [跳过] 路径无效或非 lpkx: %s" % arg)
    return files


def interactive_input():
    """交互式输入文件路径（单次输入一个，支持文件夹）"""
    files = []
    try:
        line = input("请输入或拖入 .lpkx 文件路径: ").strip()
    except (EOFError, KeyboardInterrupt):
        return files
    if not line:
        return files
    line = line.strip('"').strip("'")
    if os.path.isdir(line):
        for root, dirs, fnames in os.walk(line):
            for f in fnames:
                if f.lower().endswith(".lpkx"):
                    files.append(os.path.join(root, f))
    elif os.path.isfile(line) and line.lower().endswith(".lpkx"):
        files.append(line)
    else:
        print("  [错误] 路径不存在或非 lpkx: %s" % line)
    return files


def process_one(lpkx_path, idx, total):
    """处理单个 lpkx 文件"""
    lpkx_name = os.path.basename(lpkx_path)
    lpkx_dir = os.path.dirname(lpkx_path)
    out_name = os.path.splitext(lpkx_name)[0] + ".lpk"
    out_lpk = os.path.join(lpkx_dir, out_name)
    json_path = os.path.join(TEMP_DIR, "mid_%d.json" % idx)

    print("-" * 60)
    print("[%d/%d] %s" % (idx, total, lpkx_name))
    print("-" * 60)

    # 删除已有的 JSON
    if os.path.exists(json_path):
        os.remove(json_path)

    # Step 1: Pro 端提取
    print("  [1/2] Pro端: 解包并提取样式...")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "gbk"
    result1 = subprocess.run(
        [PRO_PYTHON, EXTRACT_SCRIPT, lpkx_path, json_path],
        capture_output=True, text=True, encoding="gbk", errors="replace", env=env
    )
    if result1.stdout:
        for line in result1.stdout.splitlines():
            print("    %s" % line)
    if result1.returncode != 0 or not os.path.exists(json_path):
        print("  [失败] Pro端处理失败")
        if result1.stderr:
            print("    %s" % result1.stderr[:300])
        return False, "Pro端提取失败"

    # Step 2: ArcMap 端构建
    print("  [2/2] ArcMap端: 重建并打包 lpk...")
    # 删除已有的 lpk
    if os.path.exists(out_lpk):
        os.remove(out_lpk)
    result2 = subprocess.run(
        [ARCMAP_PYTHON, BUILD_SCRIPT, json_path, out_lpk],
        capture_output=True, text=True, encoding="gbk", errors="replace", env=env
    )
    if result2.stdout:
        for line in result2.stdout.splitlines():
            print("    %s" % line)

    # 清理 JSON
    if os.path.exists(json_path):
        os.remove(json_path)

    if os.path.exists(out_lpk):
        size_kb = os.path.getsize(out_lpk) // 1024
        print("  [成功] 输出: %s (%d KB)" % (out_lpk, size_kb))
        return True, out_lpk
    else:
        print("  [失败] lpk 未生成")
        if result2.stderr:
            print("    %s" % result2.stderr[:300])
        return False, "ArcMap端构建失败"


def main():
    # 编码适配
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="gbk", errors="replace")
            sys.stderr.reconfigure(encoding="gbk", errors="replace")
        except Exception:
            pass

    print("=" * 60)
    print("  lpkx 转 lpk 工具（保留原 lpkx 样式）")
    print("  ArcGIS Pro -> ArcMap 图层包转换")
    print("=" * 60)
    print()

    # 检查环境
    errors = check_env()
    if errors:
        for e in errors:
            print("[错误] %s" % e)
        input("\n按回车键退出...")
        return 1

    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)

    # 收集文件
    if len(sys.argv) > 1:
        files = collect_files(sys.argv[1:])
    else:
        files = interactive_input()

    if not files:
        print("\n没有找到需要处理的 .lpkx 文件。")
        input("\n按回车键退出...")
        return 0

    total = len(files)
    print("\n共发现 %d 个文件待处理。" % total)
    print("=" * 60)

    # 逐个处理
    success = 0
    failed = 0
    results = []
    for i, f in enumerate(files, 1):
        ok, msg = process_one(f, i, total)
        results.append((os.path.basename(f), ok, msg))
        if ok:
            success += 1
        else:
            failed += 1
        print()

    # 汇总
    print("=" * 60)
    print("  处理完成")
    print("=" * 60)
    print("  成功: %d / %d" % (success, total))
    print("  失败: %d / %d" % (failed, total))
    print()
    print("详细结果:")
    for name, ok, msg in results:
        status = "成功" if ok else "失败"
        print("  [%s] %s -> %s" % (status, name, msg if not ok else "OK"))

    input("\n按回车键退出...")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
