# 自动取数来源与使用范围

## 地形：FABDEM v1.2

- 官方数据页：https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn
- 官方说明：https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/readme.txt
- 官方许可：https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/license.txt
- 数据处理去除Copernicus GLO-30中的建筑与森林高度偏差，是估计裸地地形，约30米，垂直基准EGM2008。
- 使用官方10°ZIP中的1°COG，按HTTP Range读取所需窗口，不下载整个2GB级包。
- 慢速端点通过仅绑定本机127.0.0.1的临时范围缓存服务读取，按128 KiB块校验Content-Range和长度，
  小块并行下载并保存SHA-256，失败后复用已完成块；内部本地服务随读取结束关闭。
- 公开许可CC BY-NC-SA 4.0，只用于非商业用途；商业项目需要另外授权或本地合格DTM。
- 自动报告保留Hawker et al. (2022)论文引用和原始Copernicus署名。
- 缺失超过分析区0.1%时停止；少量缺失保留NoData，不能填零构造地形。

## 建筑：3D-GloBFP 2020

- 数据索引：https://doi.org/10.5281/zenodo.11397014
- 实际分片位于Figshare，元数据与下载地址由公开API获取，CC BY 4.0。
- 保留真实多边形轮廓后栅格化，像元中心落入建筑时写相对高度；重叠取最高，不使用建筑外包矩形替代轮廓。
- 全部相交分片必须获取成功，不跳过边缘分片；ZIP完整长度和源MD5校验后才解压。
- `.part`与HTTP验证器用于恢复大文件下载。解压检查路径越界与SHP/DBF/SHX完整性。
- 原始高度为估计值，超高层可严重低估。不能用于单栋超高层精确风影评估。

## 建筑降级：CNBH-10m 2020

- 官方数据页：https://zenodo.org/records/7923866
- 使用公开API的完整文件清单，动态读取每片CRS和实际范围，CC BY 4.0。
- 10米发布网格的区域平均建筑高度产品，不等同于单栋实测高度；读取原分辨率条带后聚合到目标网格。
- 成功读取的源分片内NaN背景按非建筑处理；请求失败或分片覆盖缺失不允许填零。
- Range窗口读取不声称验证了原始整文件MD5；报告保留源MD5供追溯，目标缓存文件以自身SHA-256校验。
- 自动降级记录实际来源与原因，报告提示高层遮挡可能被削弱。

## 边界与范围

中国城市/区县边界来自阿里云DataV GeoAtlas，使用GCJ-02到WGS84近似纠偏，非测绘级法定边界。
计算外包矩形含外围来风缓冲；输出掩膜为行政区多边形，不将外包矩形及缓冲面积冒充全市面积。
自动地名解析以中国行政区为范围；其他地区可用WGS84 bbox，建筑覆盖取决于3D-GloBFP，CNBH仅覆盖中国。

缓存同时绑定完整网格、分辨率、缓冲、边界几何、数据源与处理版本。不会因为只改小数分辨率而混用旧缓存。
网络可达性随环境变化；代理优先级为--proxy、WIND_CORRIDOR_PROXY、协议环境变量（或ALL_PROXY）、已启用的Windows手动系统代理。
NO_PROXY只控制绕过目标，不会单独遮蔽系统代理地址；回环地址始终直连。Python下载与GDAL读取共用同一识别逻辑。
不内置机器专属端口，不修改Windows代理设置；PAC/WPAD和SOCKS-only需提供HTTP或混合代理端口。
