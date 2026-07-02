# lpkx → lpk 图层包转换工具

将 ArcGIS Pro 的 `.lpkx` 图层包转换为 ArcMap 的 `.lpk` 图层包，尽可能保留原始符号化样式。

## 功能

- 自动解包 `.lpkx`，提取图层定义和符号信息
- 将 CIM 符号模型转换为 webmap JSON（ArcMap 可识别格式）
- 支持三种渲染器：SimpleRenderer、UniqueValueRenderer、ClassBreaksRenderer
- HatchFill（网格填充）智能映射到 ArcMap 标准填充样式
- 批量处理多个 `.lpkx` 文件或文件夹

## 环境要求

| 组件 | 路径 | 说明 |
|------|------|------|
| Pro Python | `D:\arcpy_env\python.exe` | Python 3 + arcpy（解包 & 样式提取） |
| ArcMap Python | `C:\Python27\ArcGIS10.8\python.exe` | Python 2.7 + arcpy（重建 & 打包） |
| MXD 模板 | ArcGIS Desktop 10.8 安装目录 | 自动检测 `MapTemplates` 下可用模板 |

## 文件说明

```
lpkx to lpk/
├── lpkx转lpk工具.bat        # 主启动器（双击运行 / 拖入文件）
├── run_launcher.py           # Python 启动器（备用，编码更可靠）
├── lpkx_to_lpk_extract.py    # Pro 端：解包 lpkx → CIM JSON → webmap JSON
├── lpkx_to_lpk_build.py      # ArcMap 端：webmap JSON → MXD → lyr → lpk
└── 使用说明.md               # 详细使用说明（中文）
```

## 使用方法

### 方式一：双击 bat（推荐）

1. 双击 `lpkx转lpk工具.bat`
2. 输入或拖入 `.lpkx` 文件路径
3. 等待处理完成，`.lpk` 输出到同目录

### 方式二：拖入文件到 bat

1. 选中一个或多个 `.lpkx` 文件（或文件夹）
2. 拖到 `lpkx转lpk工具.bat` 上
3. 自动批量处理

### 方式三：Python 启动器

```bash
D:\arcpy_env\python.exe run_launcher.py "文件路径.lpkx"
```

支持多个文件和文件夹参数。

## 技术原理

```
┌─────────────────────────┐      ┌───────────────────────────┐
│   Pro 端 (Python 3)     │      │   ArcMap 端 (Python 2.7)  │
│                         │      │                           │
│  1. ExtractPackage      │      │  1. 读取中间 JSON          │
│  2. 解析 lyrx CIM JSON  │ ───→ │  2. 创建 MXD + 添加数据   │
│  3. CIM → webmap 转换   │      │  3. updateLayerFromJSON()  │
│  4. 输出中间 JSON        │      │  4. SaveToLayerFile(.lyr) │
│                         │      │  5. PackageLayer(.lpk)    │
└─────────────────────────┘      └───────────────────────────┘
```

核心技术：ArcMap 的 `Layer.updateLayerFromJSON()` 接受 webmap JSON 格式的图层定义（含 `drawingInfo.renderer`），实现跨版本的符号化重建。

## 样式保真度

### 完全支持

| 渲染器 | 保真度 |
|--------|--------|
| SimpleRenderer（单一符号） | ★★★ 高 |
| UniqueValueRenderer（唯一值） | ★★★ 高 |
| GraduatedColorsRenderer（分级色彩） | ★★★ 高 |

### 降级处理

| 符号类型 | 处理方式 |
|----------|----------|
| HatchFill（网格填充） | 映射到标准填充样式 / 降级为半透明实心 |
| PictureFill（图片填充） | 转为灰色实心 |
| CharacterMarker（字符标记） | 简化为圆形点 |

### 不支持

- 栅格 Colorizer
- 点云 / 3D 符号
- Pro 专属符号效果

## 注意事项

- 两个 Python 环境必须可用
- 处理中会在 `%TEMP%\lpkx_to_lpk` 创建临时文件
- 同名 `.lpk` 会被覆盖
- ArcMap 端输出可能出现部分乱码（Python 2.7 编码限制），不影响功能

## License

Internal tool — 内部工具。
