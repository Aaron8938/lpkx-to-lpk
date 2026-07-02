# -*- coding: utf-8 -*-
"""
lpkx_to_lpk_extract.py
======================
Pro端脚本（运行于 ArcGIS Pro 的 Python 3 环境 D:\\arcpy_env）

功能：
  1. 解包 .lpkx 文件
  2. 找到最新版本的 .lyrx 图层定义
  3. 解析 lyrx 的 CIM JSON，提取渲染器和符号信息
  4. 将 CIM 符号模型转换为 ArcMap 可识别的 webmap JSON 格式（drawingInfo）
  5. 输出中间 JSON 文件供 ArcMap 端使用

用法:
  python lpkx_to_lpk_extract.py <lpkx_path> <output_json_path>

输出 JSON 结构:
  {
    "layer_name": "...",
    "data_source": "绝对路径\\要素类",
    "workspace_path": "...",
    "dataset_name": "...",
    "geometry_type": "polygon|polyline|point|...",
    "layer_definition": {  # webmap JSON 格式
      "name": "...",
      "description": "...",
      "drawingInfo": { "renderer": {...} }
    },
    "warnings": ["..."],         # 转换中的降级提示
    "renderer_type": "simple|uniqueValue|classBreaks|unsupported",
    "extract_dir": "解包临时目录"  # 供 ArcMap 端定位数据
  }
"""
import os
import sys
import json
import shutil
import tempfile
import traceback

# ============================================================
#  Windows 控制台中文输出强制处理
#  根因：Python 3.11 在 Windows 控制台可能输出 UTF-8 字节，而 bat
#  中的 chcp 936 会让控制台按 GBK 解码，导致中文乱码。
#  解决：直接调用 Windows API WriteConsoleW 把 Unicode 字符串写到控制台，
#  不经过 Python 的 sys.stdout 编码层，因此不受任何代码页/编码设置影响。
# ============================================================
if sys.platform == "win32" and sys.version_info[0] >= 3:
    import ctypes
    from ctypes import wintypes

    _STD_OUTPUT_HANDLE = -11
    _STD_ERROR_HANDLE = -12
    _kernel32 = ctypes.windll.kernel32

    def _win_console_print(text, handle=None):
        try:
            if handle is None:
                handle = _kernel32.GetStdHandle(_STD_OUTPUT_HANDLE)
            if not text:
                text = ""
            if not text.endswith("\n"):
                text += "\n"
            # WriteConsoleW 需要 LPCWSTR (Unicode 字符串)
            buf = ctypes.create_unicode_buffer(text)
            written = wintypes.DWORD(0)
            ret = _kernel32.WriteConsoleW(handle, buf, len(text), ctypes.byref(written), None)
            if ret and written.value > 0:
                return True
        except Exception:
            pass
        # 兜底：用原始 buffer 输出 GBK 字节
        try:
            raw = text.encode("gbk", errors="replace")
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()
        except Exception:
            pass
        return False

    def _safe_print(*args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        s = sep.join(str(a) for a in args) + end
        _win_console_print(s, _kernel32.GetStdHandle(_STD_OUTPUT_HANDLE))

    def _safe_printerr(*args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        s = sep.join(str(a) for a in args) + end
        _win_console_print(s, _kernel32.GetStdHandle(_STD_ERROR_HANDLE))

    import builtins
    builtins.print = _safe_print
    sys.stderr.write = _safe_printerr

import arcpy


# ============================================================
#  CIM 颜色转换
# ============================================================
def cim_color_to_rgba(cim_color):
    """
    CIM 颜色对象 → [R, G, B, Alpha(0-255)]
    CIMRGBColor: {"type":"CIMRGBColor","values":[R,G,B,Alpha%]}
    Alpha 在 CIM 中是 0-100 的百分比，转成 0-255
    """
    if not cim_color or not isinstance(cim_color, dict):
        return [128, 128, 128, 255]  # 灰色默认

    values = cim_color.get("values", [128, 128, 128, 100])
    if len(values) >= 4:
        r, g, b, alpha_pct = values[0], values[1], values[2], values[3]
    elif len(values) == 3:
        r, g, b, alpha_pct = values[0], values[1], values[2], 100
    else:
        r, g, b, alpha_pct = 128, 128, 128, 100

    # Alpha: 0-100 → 0-255
    alpha = int(round(alpha_pct * 2.55))
    alpha = max(0, min(255, alpha))
    return [int(r), int(g), int(b), alpha]


def analyze_hatch_fills(symbol_layers):
    """
    分析 CIMHatchFill 列表，返回 (style, line_color, background_color) 或 None
    如果 hatch 角度组合能映射到 ArcMap/webmap 标准样式，返回对应 esriSFS style
    """
    hatches = []
    for layer in symbol_layers:
        if not layer.get("enable", True):
            continue
        if layer.get("type") == "CIMHatchFill":
            rotation = layer.get("rotation", 0)
            line_sym = layer.get("lineSymbol", {})
            line_layers = line_sym.get("symbolLayers", []) if line_sym else []
            line_color = None
            line_width = 1.0
            for ll in line_layers:
                if ll.get("type") == "CIMSolidStroke":
                    line_color = cim_color_to_rgba(ll.get("color"))
                    line_width = ll.get("width", 1.0)
                    break
            hatches.append({
                "rotation": rotation,
                "color": line_color,
                "width": line_width
            })
    if not hatches:
        return None

    # 标准化角度到 0~180
    rotations = sorted([(h["rotation"] or 0) % 180 for h in hatches])

    # 单一线型
    if len(rotations) == 1:
        r = rotations[0]
        if abs(r - 0) < 5:
            return ("esriSFSHorizontal", hatches[0]["color"], None)
        elif abs(r - 90) < 5:
            return ("esriSFSVertical", hatches[0]["color"], None)
        elif abs(r - 45) < 5:
            return ("esriSFSForwardDiagonal", hatches[0]["color"], None)
        elif abs(r - 135) < 5:
            return ("esriSFSBackwardDiagonal", hatches[0]["color"], None)

    # 十字交叉
    if len(rotations) == 2:
        r1, r2 = rotations[0], rotations[1]
        if (abs(r1 - 0) < 5 and abs(r2 - 90) < 5):
            return ("esriSFSCross", hatches[0]["color"], None)
        if (abs(r1 - 45) < 5 and abs(r2 - 135) < 5):
            return ("esriSFSDiagonalCross", hatches[0]["color"], None)

    # 无法精确映射
    return None


# ============================================================
#  CIM 符号 → webmap 符号
# ============================================================
def cim_symbol_to_webmap(cim_symbol, geom_type, warnings):
    """
    CIM 符号对象 → webmap JSON 符号定义
    输入: CIMSymbolReference {"type":"CIMSymbolReference","symbol":{...}}
    返回: {"type":"esriSFS/esriSLS/esriSMS", ...} 或 None
    """
    if not cim_symbol:
        return None

    # 解包 CIMSymbolReference
    if cim_symbol.get("type") == "CIMSymbolReference":
        cim_symbol = cim_symbol.get("symbol", {})

    sym_type = cim_symbol.get("type", "")
    symbol_layers = cim_symbol.get("symbolLayers", [])

    if not symbol_layers:
        return None

    # ---- 面符号 CIMPolygonSymbol ----
    if sym_type == "CIMPolygonSymbol" or geom_type == "polygon":
        fill_color = None
        fill_style = "esriSFSSolid"
        outline_color = [0, 0, 0, 255]
        outline_width = 1.0
        has_outline = False
        solid_fill_color = None
        hatch_info = None

        for layer in symbol_layers:
            if not layer.get("enable", True):
                continue
            lt = layer.get("type", "")
            if lt == "CIMSolidFill":
                solid_fill_color = cim_color_to_rgba(layer.get("color"))
                fill_color = solid_fill_color
                fill_style = "esriSFSSolid"
            elif lt == "CIMSolidStroke":
                # 轮廓线
                outline_color = cim_color_to_rgba(layer.get("color"))
                outline_width = layer.get("width", 1.0)
                has_outline = True
            elif lt == "CIMPictureFill":
                if solid_fill_color is None:
                    fill_color = [200, 200, 200, 255]
                    fill_style = "esriSFSSolid"
                warnings.append(u"图片填充已转换为灰色实心填充")

        # 单独分析 HatchFill
        hatch_info = analyze_hatch_fills(symbol_layers)
        if hatch_info:
            hatch_style, hatch_line_color, hatch_bg_color = hatch_info
            if solid_fill_color is None:
                # 只有 hatch，没有背景色 → 直接用 hatch 样式
                fill_style = hatch_style
                fill_color = hatch_line_color if hatch_line_color else [0, 0, 0, 255]
                warnings.append(u"HatchFill(网格填充)已映射为 %s 样式" % hatch_style)
            else:
                # 有背景色 + hatch，无法同时表达 → 优先保留 hatch 样式（用线色）
                fill_style = hatch_style
                fill_color = hatch_line_color if hatch_line_color else solid_fill_color
                warnings.append(u"HatchFill 与背景色共存，优先保留 %s 网格样式" % hatch_style)
        else:
            # 检查是否有 hatch 但无法映射
            has_hatch = any(l.get("type") == "CIMHatchFill" and l.get("enable", True) for l in symbol_layers)
            if has_hatch:
                line_sym = None
                for layer in symbol_layers:
                    if layer.get("type") == "CIMHatchFill" and layer.get("enable", True):
                        line_sym = layer.get("lineSymbol", {})
                        break
                line_layers = line_sym.get("symbolLayers", []) if line_sym else []
                for ll in line_layers:
                    if ll.get("type") == "CIMSolidStroke":
                        fill_color = cim_color_to_rgba(ll.get("color"))
                        if fill_color:
                            fill_color = list(fill_color)
                            fill_color[3] = max(40, fill_color[3] // 3)
                        break
                fill_style = "esriSFSSolid"
                warnings.append(u"HatchFill(网格填充)角度无法精确映射，已转换为半透明实心填充")

        if fill_color is None:
            fill_color = [200, 200, 200, 255]
            fill_style = "esriSFSHollow"
            warnings.append(u"未识别的面填充类型，使用空心符号")

        result = {
            "type": "esriSFS",
            "style": fill_style,
            "color": fill_color,
            "outline": {
                "type": "esriSLS",
                "style": "esriSLSSolid" if has_outline else "esriSLSSolid",
                "color": outline_color,
                "width": outline_width
            }
        }
        return result

    # ---- 线符号 CIMLineSymbol ----
    elif sym_type == "CIMLineSymbol" or geom_type == "polyline":
        line_color = [0, 0, 0, 255]
        line_width = 1.0
        line_style = "esriSLSSolid"

        for layer in symbol_layers:
            if not layer.get("enable", True):
                continue
            lt = layer.get("type", "")
            if lt == "CIMSolidStroke":
                line_color = cim_color_to_rgba(layer.get("color"))
                line_width = layer.get("width", 1.0)
                line_style = "esriSLSSolid"
            elif lt == "CIMDashTemplate":
                line_style = "esriSLSDash"

        return {
            "type": "esriSLS",
            "style": line_style,
            "color": line_color,
            "width": line_width
        }

    # ---- 点符号 CIMPointSymbol ----
    elif sym_type == "CIMPointSymbol" or geom_type == "point":
        marker_color = [0, 0, 0, 255]
        marker_size = 6.0
        marker_style = "esriSMSCircle"

        for layer in symbol_layers:
            if not layer.get("enable", True):
                continue
            lt = layer.get("type", "")
            if lt == "CIMSimpleMarker":
                marker_color = cim_color_to_rgba(layer.get("color"))
                marker_size = layer.get("size", 6.0)
                shape = layer.get("shape", "Circle")
                shape_map = {
                    "Circle": "esriSMSCircle",
                    "Square": "esriSMSSquare",
                    "Cross": "esriSMSCross",
                    "X": "esriSMSX",
                    "Diamond": "esriSMSDiamond",
                    "Triangle": "esriSMSTriangle",
                }
                marker_style = shape_map.get(shape, "esriSMSCircle")
            elif lt == "CIMCharacterMarker":
                marker_color = cim_color_to_rgba(layer.get("color"))
                marker_size = layer.get("size", 6.0)
                marker_style = "esriSMSCircle"
                warnings.append(u"字符标记符号已简化为圆形点符号")

        return {
            "type": "esriSMS",
            "style": marker_style,
            "color": marker_color,
            "size": marker_size,
            "angle": 0,
            "xoffset": 0,
            "yoffset": 0
        }

    return None


# ============================================================
#  CIM 渲染器 → webmap 渲染器
# ============================================================
def cim_renderer_to_webmap(cim_renderer, geom_type, warnings):
    """
    CIM 渲染器 → webmap JSON 渲染器
    返回: {"type":"simple|uniqueValue|classBreaks", ...} 或 None
    """
    if not cim_renderer:
        return None

    r_type = cim_renderer.get("type", "")

    # ---- SimpleRenderer ----
    if r_type == "CIMSimpleRenderer":
        symbol = cim_symbol_to_webmap(cim_renderer.get("symbol"), geom_type, warnings)
        if symbol:
            return {
                "type": "simple",
                "label": cim_renderer.get("label", ""),
                "description": "",
                "symbol": symbol
            }

    # ---- UniqueValueRenderer ----
    elif r_type == "CIMUniqueValueRenderer":
        fields = cim_renderer.get("fields", [])
        if not fields:
            # 有些版本用 field1/field2/field3
            f1 = cim_renderer.get("field1", "")
            fields = [f1] if f1 else []
        field1 = fields[0] if fields else ""

        infos = []
        for group in cim_renderer.get("groups", []):
            for uv in group.get("classes", []):
                values = uv.get("values", [])
                if not values:
                    continue
                # 取第一个值
                v = values[0]
                val = v.get("value", "") if isinstance(v, dict) else str(v)
                symbol = cim_symbol_to_webmap(uv.get("symbol"), geom_type, warnings)
                if symbol:
                    infos.append({
                        "value": str(val),
                        "label": uv.get("label", str(val)),
                        "description": "",
                        "symbol": symbol
                    })

        if not infos:
            warnings.append(u"唯一值渲染器无有效类别，回退为单一符号")
            return None

        renderer = {
            "type": "uniqueValue",
            "field1": field1,
            "fieldDelimiter": ",",
            "uniqueValueInfos": infos
        }

        # 默认符号
        default_sym = cim_renderer.get("defaultSymbol")
        if default_sym:
            sym = cim_symbol_to_webmap(default_sym, geom_type, warnings)
            if sym:
                renderer["defaultSymbol"] = sym
                renderer["defaultLabel"] = cim_renderer.get("defaultLabel", u"其他")

        return renderer

    # ---- ClassBreaksRenderer (分级色彩) ----
    elif r_type == "CIMClassBreaksRenderer":
        field = cim_renderer.get("field", "")
        break_infos = cim_renderer.get("breaks", [])
        if not break_infos:
            warnings.append(u"分级渲染器无断点，回退为单一符号")
            return None

        # CIM断点的结构: [{upperBound, symbol, label}, ...]
        webmap_breaks = []
        prev_upper = cim_renderer.get("minValue", 0)
        for brk in break_infos:
            upper = brk.get("upperBound", 0)
            symbol = cim_symbol_to_webmap(brk.get("symbol"), geom_type, warnings)
            if symbol:
                webmap_breaks.append({
                    "classMinValue": prev_upper,
                    "classMaxValue": upper,
                    "label": brk.get("label", ""),
                    "description": "",
                    "symbol": symbol
                })
            prev_upper = upper

        if not webmap_breaks:
            return None

        return {
            "type": "classBreaks",
            "field": field,
            "classificationMethod": "esriClassifyManual",
            "minValue": cim_renderer.get("minValue", 0),
            "classBreakInfos": webmap_breaks
        }

    else:
        warnings.append(u"不支持的渲染器类型: %s，将使用默认符号" % r_type)
        return None

    return None


# ============================================================
#  主流程
# ============================================================
def extract_lpkx(lpkx_path, output_json_path):
    warnings = []

    # Step 1: 解包 lpkx
    extract_dir = os.path.join(tempfile.gettempdir(), "lpkx_extract_%d" % os.getpid())
    # 转换为长路径名
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        GetLongPathName = ctypes.windll.kernel32.GetLongPathNameW
        if GetLongPathName(extract_dir, buf, 260):
            extract_dir = buf.value
    except Exception:
        pass
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir)

    print(u"  [1/4] 解包 lpkx ...")
    arcpy.management.ExtractPackage(lpkx_path, extract_dir)

    # Step 2: 找最新版本的 lyrx
    print(u"  [2/4] 查找 lyrx 图层定义 ...")
    lyrx_files = []
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.lower().endswith(".lyrx"):
                p = os.path.join(root, f)
                # 用所在目录名排序，pXX 越大越新
                dir_name = os.path.basename(os.path.dirname(p))
                try:
                    ver = int(dir_name.lstrip("p"))
                except ValueError:
                    ver = 0
                lyrx_files.append((ver, p))

    if not lyrx_files:
        raise RuntimeError(u"解包后未找到 .lyrx 文件")

    lyrx_files.sort(key=lambda x: x[0], reverse=True)
    lyrx_path = lyrx_files[0][1]
    print(u"        使用: %s (版本 p%d)" % (os.path.basename(lyrx_path), lyrx_files[0][0]))

    # Step 3: 解析 lyrx CIM JSON
    print(u"  [3/4] 解析渲染器和符号信息 ...")
    with open(lyrx_path, "r", encoding="utf-8") as fp:
        lyrx_data = json.load(fp)

    layer_defs = lyrx_data.get("layerDefinitions", [])
    if not layer_defs:
        raise RuntimeError(u"lyrx 中未找到 layerDefinitions")

    # 只处理第一个图层（支持多图层时取第一个）
    layer_def_cim = layer_defs[0]
    layer_name = layer_def_cim.get("name", os.path.splitext(os.path.basename(lpkx_path))[0])

    # 获取几何类型
    feature_table = layer_def_cim.get("featureTable", {})
    data_connection = feature_table.get("dataConnection", {})
    dataset_name = data_connection.get("dataset", "")
    workspace_conn_str = data_connection.get("workspaceConnectionString", "")  # DATABASE=..\commondata\xxx.gdb
    ws_factory = data_connection.get("workspaceFactory", "")

    # 解析数据源绝对路径
    # workspaceConnectionString 格式: DATABASE=..\commondata\凯尔仕.gdb
    gdb_relative = ""
    if "DATABASE=" in workspace_conn_str:
        gdb_relative = workspace_conn_str.split("DATABASE=", 1)[1].strip()

    # lyrx 所在目录
    lyrx_dir = os.path.dirname(lyrx_path)
    # 相对路径基于 lyrx 所在目录解析
    if gdb_relative:
        gdb_abs = os.path.normpath(os.path.join(lyrx_dir, gdb_relative))
    else:
        # 遍历找 .gdb
        gdb_abs = None
        for root, dirs, files in os.walk(extract_dir):
            for d in dirs:
                if d.lower().endswith(".gdb"):
                    gdb_abs = os.path.join(root, d)
                    break
            if gdb_abs:
                break

    if not gdb_abs or not os.path.exists(gdb_abs):
        raise RuntimeError(u"未找到数据源 GDB: %s" % gdb_relative)

    # 转换为长路径名（避免 ADMINI~1 短路径在 ArcMap 端出问题）
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        GetLongPathName = ctypes.windll.kernel32.GetLongPathNameW
        if GetLongPathName(gdb_abs, buf, 260):
            gdb_abs = buf.value
    except Exception:
        pass

    data_source = os.path.join(gdb_abs, dataset_name)

    # 获取几何类型
    desc = arcpy.Describe(data_source)
    geom_type = desc.shapeType.lower()  # polygon / polyline / point / multipoint
    print(u"        图层: %s | 数据: %s | 几何: %s" % (layer_name, dataset_name, geom_type))

    # 获取渲染器
    cim_renderer = layer_def_cim.get("renderer")
    colorizer = layer_def_cim.get("colorizer")  # 栅格用

    renderer_type = "unsupported"
    webmap_renderer = None

    if cim_renderer:
        webmap_renderer = cim_renderer_to_webmap(cim_renderer, geom_type, warnings)
        r_type = cim_renderer.get("type", "")
        if r_type == "CIMSimpleRenderer":
            renderer_type = "simple"
        elif r_type == "CIMUniqueValueRenderer":
            renderer_type = "uniqueValue"
        elif r_type == "CIMClassBreaksRenderer":
            renderer_type = "classBreaks"
    elif colorizer:
        warnings.append(u"栅格渲染器(colorizer)暂不支持转换，使用默认符号")
        renderer_type = "unsupported"

    # 如果没有渲染器或转换失败，用默认 simple renderer
    if not webmap_renderer:
        # 创建一个默认符号
        if geom_type == "polygon":
            default_sym = {
                "type": "esriSFS", "style": "esriSFSSolid",
                "color": [200, 200, 200, 255],
                "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": [0, 0, 0, 255], "width": 1}
            }
        elif geom_type in ("polyline",):
            default_sym = {"type": "esriSLS", "style": "esriSLSSolid", "color": [0, 0, 0, 255], "width": 1}
        else:
            default_sym = {"type": "esriSMS", "style": "esriSMSCircle", "color": [0, 0, 0, 255], "size": 6}
        webmap_renderer = {"type": "simple", "label": "", "description": "", "symbol": default_sym}
        if not warnings:
            warnings.append(u"使用默认符号")

    # Step 4: 构建输出 JSON
    print(u"  [4/4] 生成 webmap JSON 定义 ...")
    output = {
        "layer_name": layer_name,
        "data_source": data_source,
        "workspace_path": gdb_abs,
        "dataset_name": dataset_name,
        "geometry_type": geom_type,
        "renderer_type": renderer_type,
        "extract_dir": extract_dir,
        "warnings": warnings,
        "layer_definition": {
            "name": layer_name,
            "description": u"Converted from lpkx",
            "drawingInfo": {
                "renderer": webmap_renderer
            }
        }
    }

    with open(output_json_path, "w", encoding="utf-8") as fp:
        json.dump(output, fp, ensure_ascii=False, indent=2)

    print(u"  [OK] 中间 JSON 已输出: %s" % output_json_path)
    if warnings:
        print(u"  [!] 转换提示:")
        for w in warnings:
            print(u"      - %s" % w)

    return output


# ============================================================
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(u"用法: python lpkx_to_lpk_extract.py <lpkx路径> <输出JSON路径>")
        sys.exit(1)

    lpkx_path = sys.argv[1]
    output_json = sys.argv[2]

    if not os.path.exists(lpkx_path):
        print(u"错误: 文件不存在 - %s" % lpkx_path)
        sys.exit(1)

    print(u"=" * 50)
    print(u"Pro端: 解包并提取样式信息")
    print(u"=" * 50)
    print(u"输入: %s" % lpkx_path)

    try:
        result = extract_lpkx(lpkx_path, output_json)
        print(u"\n[OK] Pro端处理完成")
        # 输出标记行供 bat 解析
        print(u"__EXTRACT_OK__")
    except Exception as e:
        print(u"\n[X] Pro端处理失败: %s" % str(e))
        traceback.print_exc()
        print(u"__EXTRACT_FAIL__")
        sys.exit(1)
