---
name: wind-corridor-native
description: 根据一句话自动获取中国城市行政边界、去建筑树木的地形和建筑高度数据，调用本地原生引擎筛选通风廊道，自动出图和报告；也支持本地栅格。适用于城市风道、通风廊道和相对通风条件筛选，不用于真实速度场或风压计算。
---

# 城市风环境通风廊道

用户给出城市、风来向、风速和分辨率后，直接开始，不再要求用户准备数据或逐项确认默认参数。
使用PowerShell入口自动执行环境检查。缺少系统/Python、运行文件损坏或目录不可写时，展示缺项和处理方法后停止；不要跳过检查继续模拟。
首次使用可先只检查环境，不安装依赖、不下载数据、不模拟：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<技能目录>/scripts/start.ps1" -CheckOnly
```

未安装Python时，此入口会提示需要64位Python 3.12及可用命令。任何运行环境缺项均提示后停止，返回非零退出码；由用户自行决定安装方式。只检查用户选定的Python环境，不安装软件、创建虚拟环境、切换镜像或自动切换到其他解释器。宿主AI也应仅说明缺项，不代替用户安装。
例如「我希望模拟整个武汉市的东风，风速在3m/s，分辨率是30米」：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<技能目录>/scripts/start.ps1" --describe "我希望模拟整个武汉市的东风，风速在3m/s，分辨率是30米"
```

也可由宿主AI理解口语后直接映射成参数，以支持描述解析器未涵盖的自然表达：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<技能目录>/scripts/start.ps1" --city 武汉市 --wind-from E --wind-speed 3 --res 30
```

路径以技能所在目录解析脚本位置。默认40轮、seed 42、1000米外围缓冲；分辨率缺省30米。
需要Windows x64及Python 3.12+；依赖要求见requirements.txt。用户可按自己的方式配置环境，再激活该环境或使用其Python解释器调用scripts/start.py。
未指定输出目录时自动创建当前工作目录下wind-results内的新任务目录。
城市或风来向确实不明确时才追问；「主城区」需要明确的区县或bbox，不能擅自当作整个城市。
「东风」是从东边来，参数E；区县应包含所属市名，例如武汉市江岸区。

## 自动流程

1. 查询行政边界并纠正坐标，建立米制计算网格与行政区掩膜。
2. 从官方端点按窗口获取FABDEM v1.2去建筑/树木偏差的约30米地形。
3. 优先获取3D-GloBFP建筑轮廓与估计高度；取数不可用时自动切换CNBH-10m，并在报告明确降级。
4. 对齐输入并校验覆盖，调用随包C++二进制计算；自动按内存限制并发。
5. 输出行政区内成果和统计，自动生成PNG、来源记录和report.md。

数据源、许可与局限见 [references/data-sources.md](references/data-sources.md)。FABDEM公开数据为非商业许可：
默认流程用于研究/演示；商业项目需要另行授权的地形源，可使用本地DTM模式。不要把数据许可说成引擎许可。
不随技能分发下载后的栅格或用户数据。网络失败保留分片检查点，不能用随机数据、全零建筑或未经去建筑的DSM顶替。

## 缓存与恢复

默认缓存为用户目录下`.wind-corridor-native/cache`，可用`--cache-dir`修改。
缓存包含完整网格、边界、缓冲和数据源标识；复用前校验网格和SHA-256。
原始大文件下载支持断点续传；窗口读取完成的地形/建筑分片独立缓存，失败后不重做已完成分片。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<技能目录>/scripts/start.ps1" --resume --out "<原任务目录>"
```

恢复时读取保存的描述参数，已完成且哈希通过的计算直接复用。修改研究参数需新建任务。
任务失败应说明失败阶段及恢复命令，不能把旧成果当成本次成功。
自动模式还支持`--bbox minlon,minlat,maxlon,maxlat --bbox-label 名称`、`--buffer`、`--building-source auto|3dglobfp|cnbh`、`--proxy`。
代理按`--proxy`、`WIND_CORRIDOR_PROXY`、协议环境变量HTTP_PROXY/HTTPS_PROXY（或ALL_PROXY）、Windows已启用的手动系统代理的顺序选择。
仅设置NO_PROXY不会使Windows系统代理失效；尊重目标站点的代理绕过设置，本机127.0.0.1/localhost服务始终直连。
自动识别支持Windows中Clash等配置的HTTP/HTTPS代理，不猜测固定端口；PAC/WPAD和SOCKS-only需提供HTTP代理地址。
不要将含凭据的代理地址写入报告。既有任务会复用已校验的来源缓存；要验证首选建筑源恢复，使用新任务并指定`--building-source 3dglobfp`。

## 本地数据与成果

本地数据模式保留：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<技能目录>/scripts/start.ps1" --dem "<裸地DTM.tif>" --buildings "<相对高度.tif>" --wind-from N --out "<新输出目录>"
```

需要通用重投影/网格对齐时运行scripts/prepare_data.py，参数见 [references/interface.md](references/interface.md)。
没有建筑的场景仅在用户明确要求时使用`--terrain-only`。

自动任务目录内task_state.json记录阶段，result_pointer.json指出成果子目录。
成果包含GeoTIFF、PNG、report.md/result.json，data_sources.json保存来源、年代、降级原因及覆盖信息。
面积只统计行政区有效像元；外围缓冲参与模拟但不计入行政区面积。

输入风速通过内部经验策略影响结果，输出仍为无量纲路径累积/通风廊道指标，不能解释为m/s实测风速、风压或舒适度。
建筑高度是遥感估计，超高层可能明显偏低；30米地形重采样为更细格网不会增加真实精度。
计算核心仅提供Windows x64二进制；不自行重写算法替代引擎，不需要用户看到内部公式或权重。
