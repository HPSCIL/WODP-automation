# 任务接口 v1

Windows x64。原生引擎无需Python；数据准备与出图脚本需要requirements.txt中的依赖。
`bin/wind-engine.exe --version` 查看版本。`bin/wind-engine.exe run --config job.json` 直接计算。

```json
{
  "schema_version": 1,
  "dem": "data/dtm.tif",
  "terrain_kind": "DTM",
  "buildings": "data/buildings.tif",
  "wind_from": "N",
  "wind_speed_ms": 3,
  "rounds": 40,
  "seed": 42,
  "threads": 4,
  "output_dir": "results/scenario-n"
}
```

所有相对路径按JSON所在目录解析。dem必须为用户确认的裸地DTM。
`buildings`可用`"terrain_only":true`替代，不能同时出现。
可选`analysis_mask`为同网格0/1分析区；可选`thresholds`为非负归一化指标阈值数组（最多32个）。
可选`normalization_max`为归一化指标上限，无物理速度单位；不同任务比较时应使用一致约定。
threads自动受内存和硬件限制，实际值写入result.json。
可选wind_speed_ms必须是有限正数，通过二进制内经验规则影响计算；不输出物理风速场。
未知键、非法参数、错位网格、经纬度及英尺坐标、全零建筑文件会被拒绝。

输入均为北向上、正方形像元、米制投影、单波段GeoTIFF，至少3×3。
0建筑表示无建筑；NoData表示未知，不可互换。分析区内建筑未知值需补充真实数据。
地形缺失区及相邻像元不参与路径路由，无效区维持NoData。

输出文件：acc_total.tif（整数累计）、acc_mean.tif、normalized.tif、corridor_otsu.tif、
corridor_threshold_N.tif（可选）及result.json。二值图中0/1均有效，255无效。
有analysis_mask时，成果输出与面积分母仅使用掩膜内有效像元；外围仍参与模拟。
脚本补充acc_mean.png、corridor_otsu.png和report.md。
`python scripts/render_report.py --out <已有成果目录>`可重生成图和报告。

标准输出采用JSON Lines，event为stage/progress/complete/error。
退出码0成功、2输入或配置错误、3计算或文件错误。只有成功运行才生成成功result.json。
不同任务必须使用不同的新输出目录。

## 一句话自动任务

`python scripts/run_wind.py --describe "模拟整个武汉市的东风，风速3m/s，分辨率30米"`

等价结构化入口：`--city 武汉市 --wind-from E --wind-speed 3 --res 30`。
自动取数范围、建筑源、缓存、输出目录由Python任务层管理；地理数据准备后仍使用上述原生JSON接口。
自动层的city/bbox/cache_dir等不是原生引擎配置键，不能直接写入原生job.json。
恢复：`python scripts/run_wind.py --resume --out <原任务目录>`。
推荐使用带环境检查的启动入口：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1`，后接相同模拟参数。
仅检查：PowerShell入口的`-CheckOnly`或Python入口`python scripts/start.py --check-env`。通过返回0，未就绪返回2；仅检查不会安装依赖、下载数据或模拟。
自动任务根目录含task_state.json、data_sources.json、boundary.geojson、引擎日志、job-N.json和result_pointer.json。
原生成果位于run-N子目录，失败尝试保留供诊断，新尝试不会把不完整输出当作成功。

## 本地建筑输入（技能包1.2.0）

`--buildings`接受SHP/GPKG/GeoJSON或相对高度栅格；矢量能力需用户自行准备requirements-vector.txt中的可选依赖。
单一高度候选可自动识别；字段含义/单位有歧义时，使用`--height-field 字段 --height-unit m|floors`。floors必须指定有限正数`--floor-height`，单位米/层。
GPKG含多图层时必需`--building-layer 图层名`。SHP需要同名shp/shx/dbf/prj，编码文件cpg可选。
矢量必需`--confirm-building-coverage`，表示用户已核实数据包含整个计算区域及缓冲内全部建筑；这不是程序自动验证覆盖完整。缺少声明则提示后停止，不猜测空白区域是否漏楼。
真实多边形按像元中心栅格化；洞与建筑间隙保留，重叠取最高；小于格网的建筑可能没有像元中心落入，需合理选择分辨率。
空/非面/无效几何和缺失/非正高度不会静默跳过；不自动推断层高或回填建筑高度。

本地模式：`--dem DTM.tif --buildings buildings.shp --confirm-building-coverage --res 10 --wind-from E --out 新目录`。
混合模式：`--city 武汉市武昌区 --buildings buildings.gpkg --building-layer buildings --height-field height --confirm-building-coverage --res 10 --wind-from E --wind-speed 3`。
本地建筑与`--building-source`互斥，混合模式仍自动下载地形。--resume仅给原目录会恢复原文件及参数并检查源文件SHA-256；文件修改后需新建任务。
用户明确选择纯地形时支持`--city/--bbox --terrain-only`或`--dem DTM.tif --terrain-only`；在线建筑失败不会自动变为纯地形。
本地`--crs/--res`或矢量输入触发统一数据准备，准备文件放在`<输出目录名>.inputs`，不会覆盖已有目录。若带分析掩膜，请先独立prepare_data.py并提供与准备栅格匹配的掩膜。
上述选项属于Python数据准备层，原生JSON接口仍只接受标准DTM/高度栅格；生产引擎没有新增算法接口。
