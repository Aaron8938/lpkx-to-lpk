# -*- coding: utf-8 -*-
"""
lpkx_to_lpk_build.py
====================
ArcMap端脚本（运行于 ArcMap 10.8 的 Python 2.7 环境 C:\\Python27\\ArcGIS10.8）

功能:
  1. 读取 Pro 端生成的中间 JSON
  2. 创建 MXD，添加数据图层
  3. 用 updateLayerFromJSON 应用符号化（保留颜色/渲染器）
  4. SaveToLayerFile 生成 .lyr
  5. PackageLayer 生成 .lpk，输出到指定路径

用法:
  python lpkx_to_lpk_build.py <input_json> <output_lpk_path>
"""
import arcpy
import arcpy.mapping as mapping
import os
import sys
import json
import io
import shutil
import tempfile
import traceback

# Python 2.7 中文编码修复
# cmd控制台是GBK编码，所以stdout用gbk输出
if sys.version_info[0] < 3:
    reload(sys)
    sys.setdefaultencoding('utf-8')
    import codecs
    # 用gbk输出到控制台，errors='replace'避免崩溃
    sys.stdout = codecs.getwriter('gbk')(sys.stdout, errors='replace')
    sys.stderr = codecs.getwriter('gbk')(sys.stderr, errors='replace')


def to_safe_str(s):
    """安全转字符串：处理GBK/UTF-8混合编码的路径"""
    if s is None:
        return u""
    if isinstance(s, unicode):
        return s
    if isinstance(s, str):
        for enc in ['utf-8', 'gbk', 'cp936', 'latin-1']:
            try:
                return s.decode(enc)
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        return s.decode('latin-1', errors='replace')
    return unicode(s)


def safe_print(msg):
    """安全打印：避免编码崩溃"""
    try:
        print(msg)
    except (UnicodeDecodeError, UnicodeEncodeError):
        try:
            print(to_safe_str(msg))
        except Exception:
            print(u"[print error]")

# MXD 模板路径
TEMPLATE_MXD = r"C:\Program Files (x86)\ArcGIS\Desktop10.8\MapTemplates\Standard Page Sizes\Architectural Page Sizes\ARCH A Landscape.mxd"

# 备选模板
TEMPLATE_MXD_ALT = r"C:\Program Files (x86)\ArcGIS\Desktop10.8\MapTemplates\Standard Page Sizes\ANSI Landscape\ANSI A Landscape.mxd"


def get_template_mxd():
    """获取可用的 MXD 模板路径"""
    if os.path.exists(TEMPLATE_MXD):
        return TEMPLATE_MXD
    if os.path.exists(TEMPLATE_MXD_ALT):
        return TEMPLATE_MXD_ALT
    # 搜索任意 mxd
    base = r"C:\Program Files (x86)\ArcGIS\Desktop10.8\MapTemplates"
    for root, dirs, files in os.walk(base):
        for f in files:
            if f.lower().endswith(".mxd"):
                return os.path.join(root, f)
    raise RuntimeError(u"未找到 MXD 模板文件")


def build_lpk(input_json_path, output_lpk_path):
    safe_print(u"  [1/5] 读取中间 JSON ...")
    with io.open(input_json_path, "r", encoding="utf-8") as fp:
        info = json.load(fp)

    data_source = info["data_source"]
    layer_name = info.get("layer_name", u"layer")
    layer_def = info["layer_definition"]
    warnings = info.get("warnings", [])
    renderer_type = info.get("renderer_type", "simple")

    # 用 arcpy.Exists 检查（GDB 内要素类不是文件系统文件）
    if not arcpy.Exists(data_source):
        # 尝试短路径转长路径
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.kernel32.GetLongPathNameW(data_source, buf, 260)
            long_ds = buf.value
            if long_ds and arcpy.Exists(long_ds):
                data_source = long_ds
            else:
                raise RuntimeError(u"数据源不存在")
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError(u"数据源不存在")

    safe_print(u"        图层: %s | 渲染器: %s" % (to_safe_str(layer_name), to_safe_str(renderer_type)))

    # Step 2: 创建 MXD
    safe_print(u"  [2/5] 创建 MXD 工作环境 ...")
    tmpl = get_template_mxd()
    tmp_dir = tempfile.gettempdir()
    work_mxd = os.path.join(tmp_dir, "lpk_build_%d.mxd" % os.getpid())
    shutil.copy2(tmpl, work_mxd)

    mxd = mapping.MapDocument(work_mxd)
    df = mapping.ListDataFrames(mxd)[0]

    # Step 3: 添加数据 + 应用符号
    safe_print(u"  [3/5] 添加数据并应用符号化 ...")
    layer = mapping.Layer(data_source)
    mapping.AddLayer(df, layer, "AUTO_ARRANGE")
    arcpy.RefreshActiveView()
    arcpy.RefreshTOC()

    layers = mapping.ListLayers(mxd, "*", df)
    if not layers:
        raise RuntimeError(u"添加数据失败")

    lyr = layers[0]

    # 设置 description（PackageLayer 必需）
    lyr.description = layer_def.get("description", u"Converted from lpkx")

    # 用 updateLayerFromJSON 应用符号化
    json_str = json.dumps(layer_def)
    try:
        lyr.updateLayerFromJSON(json_str)
        arcpy.RefreshActiveView()
        arcpy.RefreshTOC()
        safe_print(u"        符号化应用成功 (type=%s)" % to_safe_str(lyr.symbologyType))
    except Exception as e:
        safe_print(u"        [警告] updateLayerFromJSON 失败: %s" % to_safe_str(str(e))[:100])
        warnings.append(u"符号化应用失败，使用默认符号")

    # 重新设置 description（updateLayerFromJSON 可能覆盖）
    lyr.description = layer_def.get("description", u"Converted from lpkx")
    # 确保名称正确
    try:
        lyr.name = layer_name
    except:
        pass

    # 保存 MXD
    mxd.save()

    # Step 4: SaveToLayerFile
    safe_print(u"  [4/5] 生成 .lyr 文件 ...")
    tmp_lyr = os.path.join(tmp_dir, "lpk_build_%d.lyr" % os.getpid())
    if os.path.exists(tmp_lyr):
        os.remove(tmp_lyr)
    arcpy.management.SaveToLayerFile(lyr, tmp_lyr)
    safe_print(u"        lyr size: %d bytes" % os.path.getsize(tmp_lyr))

    # Step 5: PackageLayer
    safe_print(u"  [5/5] 打包 .lpk ...")
    if os.path.exists(output_lpk_path):
        os.remove(output_lpk_path)

    # 确保输出目录存在
    out_dir = os.path.dirname(output_lpk_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    try:
        arcpy.management.PackageLayer(lyr, output_lpk_path)
        lpk_size = os.path.getsize(output_lpk_path)
        safe_print(u"        lpk size: %d bytes" % lpk_size)
    except Exception as e:
        raise RuntimeError(u"PackageLayer failed: %s" % to_safe_str(str(e))[:200])

    # 清理临时 MXD
    try:
        del mxd
        os.remove(work_mxd)
    except:
        pass

    return {
        "output_lpk": to_safe_str(output_lpk_path),
        "lpk_size": os.path.getsize(output_lpk_path),
        "warnings": warnings,
        "renderer_type": renderer_type,
        "symbologyType": to_safe_str(lyr.symbologyType)
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        safe_print(u"用法: python lpkx_to_lpk_build.py <input_json> <output_lpk>")
        sys.exit(1)

    input_json = to_safe_str(sys.argv[1])
    output_lpk = to_safe_str(sys.argv[2])

    if not os.path.exists(input_json):
        safe_print(u"错误: JSON 文件不存在")
        sys.exit(1)

    safe_print(u"=" * 50)
    safe_print(u"ArcMap 端: 重建图层并打包 lpk")
    safe_print(u"=" * 50)

    try:
        result = build_lpk(input_json, output_lpk)
        safe_print(u"\n[OK] ArcMap 端处理完成")
        safe_print(u"  输出: %s" % result["output_lpk"])
        safe_print(u"  大小: %s bytes" % result["lpk_size"])
        if result["warnings"]:
            safe_print(u"  提示:")
            for w in result["warnings"]:
                safe_print(u"      - %s" % to_safe_str(w))
        safe_print(u"__BUILD_OK__")
    except Exception as e:
        safe_print(u"\n[失败] %s" % to_safe_str(str(e)))
        traceback.print_exc()
        safe_print(u"__BUILD_FAIL__")
        sys.exit(1)
