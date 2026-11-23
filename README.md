# 城市通风廊道 Skill

根据一句话描述，自动获取研究区边界、地形和建筑数据，调用本地 C++ 引擎，生成通风廊道筛选图、GeoTIFF 和报告。

技能包版本 **1.1.3**，计算引擎版本 **1.1.0**。技能入口为 [SKILL.md](SKILL.md)。

## 运行条件

- Windows x64，推荐使用已验证的 **64 位 Python 3.12**，且 `python` 命令可用。
- 运行所需 Python 依赖及版本见 `requirements.txt`。安装方式和环境管理工具由用户自行决定。
- 自动取数需要访问 DataV、Bristol、Figshare 或 Zenodo。需要代理时，在自己的电脑上启用系统代理。
- 计算引擎、GIS 动态库、运行资源和第三方许可已包含在 `bin/` 中，无需编译 C++ 或准备原开发项目。

完整克隆或下载仓库，保留 `bin/`、`scripts/`、`references/` 与 `SKILL.md` 的相对位置。注册到支持 `SKILL.md` 的宿主时，选择整个目录。

## 快速开始

在仓库根目录打开 PowerShell，首次使用建议先仅检查环境：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1 -CheckOnly
```

即使电脑没有 Python，该入口也会提示需要 64 位 Python 3.12 及可用命令，由用户自行选择如何准备环境。检查会核对 Windows x64、Python 版本和位数、依赖版本及实际导入、引擎启动、GIS 动态库加载、运行文件完整性、输出/缓存/临时目录写入权限和代理配置。

每个缺项都会附处理方法。仅检查模式不安装依赖、不下载数据、不启动模拟；环境未就绪时返回退出码 `2`，通过时返回 `0`。此检查不会实际请求外部数据网站，网络可达性在取数时验证。

正式运行时也会自动先检查。任何环境缺项都会给出提示并停止；技能不会安装软件或依赖、创建虚拟环境、切换软件包镜像，也不会自动改用其他 Python 环境。请自行准备好环境，再激活该环境或使用选定的 Python 解释器启动。运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1 --describe "我希望模拟整个武汉市的东风，风速在3m/s，分辨率是30米"
```

武昌区 10 米网格示例：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1 --city "武汉市武昌区" --wind-from E --wind-speed 3 --res 10
```

`E` 表示风从东边吹来。默认模拟 40 轮、随机种子 42、外围缓冲 1000 米；结果统计限定在行政区内。

未指定输出目录时，会在当前工作目录的 `wind-results/` 中创建新任务。完成后查看终端给出的 `report.md`，其中包含图件、面积统计和实际数据来源。

下载缓存默认位于用户目录的 `.wind-corridor-native/cache`。失败后，使用终端中的原任务目录恢复：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1 --resume --out "原任务目录的完整路径"
```

支持本地 DTM、建筑高度栅格及更多参数，见 [接口说明](references/interface.md) 和 [技能说明](SKILL.md)。
已有可用 Python 时，也可运行 `python -X utf8 scripts/start.py --check-env` 或通过同一入口传入模拟参数。检查和任务均使用用户选定的同一 Python 环境，包括其用户级安装和 `PYTHONPATH` 配置。

## 网络与代理

代理按以下优先级选择：`--proxy` → `WIND_CORRIDOR_PROXY` → `HTTP_PROXY` / `HTTPS_PROXY`（或 `ALL_PROXY`）→ 已启用的 Windows 手动系统代理。

支持读取 Clash 等工具写入的系统代理配置，不绑定某台电脑或某个固定端口。`NO_PROXY` 只控制绕过目标，不会单独遮蔽系统代理地址；本机缓存服务始终直连。

PAC/WPAD 和仅提供 SOCKS 的配置需要另行提供 HTTP 或混合代理地址。此处的代理仅用于运行时数据获取；依赖安装及其网络配置由用户自行安排。不要将代理账号、密码或个人环境配置提交到仓库。

## 数据来源与适用范围

- 边界：DataV 中国行政区边界，经坐标近似纠偏，非测绘级法定边界。
- 地形：FABDEM v1.2，约 30 米，去除建筑与森林高度偏差的估计地形。
- 建筑：优先采用 3D-GloBFP 单体轮廓与估计高度；不可用时自动切换到 CNBH-10m，并在报告中记录原因。

既有任务会复用已校验的来源缓存。需要强制验证首选建筑源时，新建任务并添加 `--building-source 3dglobfp`。

建筑数据主要对应 2020 年，不能代表所有现状建筑；估计高度可能低估高层遮挡。10 米计算网格中的地形由约 30 米数据重采样，不增加地形的实际精度。

输出是相对通风廊道指标，不是真实风速、风压或行人舒适度。风速输入通过引擎内经验策略影响结果。

完整数据来源与许可见 [数据源说明](references/data-sources.md)。**默认 FABDEM 数据采用 CC BY-NC-SA 4.0，适用于非商业用途；商业项目需另行授权的数据或合格本地 DTM。** 数据许可与引擎授权分别适用。

## 验证状态

已在本机 Windows / Python 3.12 环境跑通武汉全市 30 米和武昌区 10 米任务，两次建筑数据均使用 CNBH。代理修复后，已验证自动代理下的 Figshare 索引与建筑 ZIP 文件头读取，以及代理路由、GDAL、任务恢复和技能闭环回归。

尚未完成全新电脑的安装验收，也未完成首选 3D-GloBFP 的完整分片下载和模拟验收。网络可用性与数据覆盖仍会影响首次使用。

## 仓库内容与版本发布

`scripts/` 包含启动、取数、编排和绘图代码；专有计算核心以 `bin/wind-engine.exe` 提供。`bin/` 必须完整保留，第三方许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 `bin/licenses/`。

`release-manifest.json` 记录随包运行文件的 SHA-256，不包含新增的仓库维护文档。`.gitattributes` 保留文件原始字节，避免 Git 换行转换使发布清单失效。

仓库不收录用户数据、缓存、模拟成果、虚拟环境或私有引擎开发工程。版本 ZIP 可作为 GitLab 版本下载附件发布，无需重复提交到 Git 历史。
