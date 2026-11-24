"""Render existing engine outputs; does not implement any simulation method."""
from pathlib import Path
import argparse
import json

def render(output,make_plots=True):
    output=Path(output)
    result=json.loads((output/'result.json').read_text(encoding='utf8'))
    if result.get('status')!='success':raise ValueError('No successful engine result')
    p=result['parameters'];grid=result['grid'];stats=result['statistics']
    lines=['# 风环境通风廊道筛选报告','',
           '本结果用于相对通风廊道筛选，不代表真实风速、风压或行人舒适度。','',
           f"引擎版本：{result['engine_version']}；风来向：{p['wind_from']}；轮数：{p['rounds']}；随机种子：{p['seed']}；实际线程：{p['threads']}。",'',
           f"网格：{grid['width']}×{grid['height']}，分辨率 {grid['resolution_m']:g} m，有效分析像元 {grid['valid_cells']:,}。",'',
           '## 输入与来源校验','']
    if p.get('wind_speed_ms') is not None:
        lines[4:4]=[f"输入风速：{p['wind_speed_ms']:g} m/s；通过内部经验策略影响模拟，不是求解真实速度场。",'']
    for role,item in result['inputs'].items():
        lines.extend([f"- {role}：`{item['path']}`",f"  SHA-256：`{item['sha256']}`"])
    lines.extend(['','地形类型按输入合同声明为裸地DTM；程序无法仅凭像元值证明数据已去除建筑。',
                  '数据来源、采集时间和高度误差由数据提供方核实。','','## 累积指标','',
                  f"累计峰值：{stats['acc_total_max']}；平均累积峰值：{stats['acc_mean_max']:.4f}。",'',
                  '归一化强度为无量纲模型指标，不能以m/s解释。','','## 风道提取','',
                  '| 结果 | 阈值 | 面积 km² | 有效区占比 |','|---|---:|---:|---:|'])
    for c in result['corridors']:
        lines.append(f"| {c['file']} | {c['threshold']:.6g} | {c['area_km2']:.6f} | {c['ratio']:.2%} |")
    lines.extend(['','面积与占比分母仅使用有效分析区；若未提供analysis_mask，分析区为输入网格的有效覆盖。'])
    preparation_path=output/'preparation.json'
    if preparation_path.is_file():
        preparation=json.loads(preparation_path.read_text(encoding='utf8'))
        building=preparation.get('building_preparation')
        if building:
            lines.extend(['','## 本地建筑数据准备','',f"原始建筑：`{building['source']['path']}`；源文件SHA-256及准备规则见preparation.json。"])
            if building.get('height_field'):
                lines.append(f"图层：{building['layer']}；字段：{building['height_field']}；单位：{building['height_unit']}；转换为米的系数：{building['height_scale_to_m']:g}。")
            lines.extend(['- '+w for w in building.get('warnings',[])])
    source_path=output/'data_sources.json'
    source_info=None
    if source_path.is_file():
        source_info=json.loads(source_path.read_text(encoding='utf8'))
        lines.extend(['','## 自动取数与研究区','',f"研究区：{source_info['place']['name']}；边界坐标处理：{source_info['place']['coord_fix']}。",''])
        for role,label in [('terrain','地形'),('buildings','建筑')]:
            source=source_info[role]
            lines.extend([f"- {label}：[{source['name']}]({source['url']})",f"  许可：{source['license']}。"])
            if source.get('semantics'):lines.append('  数据含义：'+source['semantics'])
            if source.get('attribution'):lines.append('  '+source['attribution'])
            if source.get('citation'):lines.append('  '+source['citation'])
            if source.get('fallback_reason'):lines.append('  首选3D-GloBFP不可用，已自动降级；原因：'+source['fallback_reason'])
            if source.get('height_field'):
                lines.append(f"  图层：{source['layer']}；高度字段：{source['height_field']}；原单位：{source['height_unit']}；转换为米系数：{source['height_scale_to_m']:g}；完整覆盖由用户声明（含外围缓冲）。")
        lines.extend(['','数据覆盖和分片来源见data_sources.json；统计已使用行政边界掩膜，外部缓冲只参与模拟。'])
        lines.extend(['- '+w for w in source_info.get('warnings',[])])
    if result['warnings']:
        lines.extend(['','## 运行提示','']+['- '+s for s in result['warnings']])
    if make_plots:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        import numpy as np
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.warp import reproject
        from affine import Affine
        from matplotlib.colors import ListedColormap
        fonts={f.name for f in font_manager.fontManager.ttflist}
        for family in ['Microsoft YaHei','SimHei','Noto Sans CJK SC']:
            if family in fonts:plt.rcParams['font.family']=[family,'DejaVu Sans'];break
        plt.rcParams['axes.unicode_minus']=False
        for name,title,binary in [('acc_mean','Mean path accumulation',False),('corridor_otsu','Corridor screening',True)]:
            with rasterio.open(output/(name+'.tif')) as src:
                # Downsample only for display; calculations and statistics use original rasters.
                scale=min(1.,1600/max(src.width,src.height))
                if scale<1:
                    nh=max(1,int(src.height*scale));nw=max(1,int(src.width*scale))
                    display=np.full((nh,nw),np.nan,dtype='float32')
                    reproject(rasterio.band(src,1),display,src_transform=src.transform,src_crs=src.crs,src_nodata=src.nodata,
                              dst_transform=src.transform*Affine.scale(src.width/nw,src.height/nh),dst_crs=src.crs,dst_nodata=np.nan,
                              resampling=Resampling.average)
                    a=np.ma.masked_invalid(display)
                else:a=src.read(1,masked=True)
                width=src.bounds.right-src.bounds.left;height=src.bounds.top-src.bounds.bottom
            fig,ax=plt.subplots(figsize=(9,7),constrained_layout=True)
            if binary:im=ax.imshow(a,extent=(0,width,0,height),cmap='OrRd' if scale<1 else ListedColormap(['#eef2f6','#e65e39']),vmin=0,vmax=1,interpolation='nearest')
            else:
                values=a.compressed();vmax=float(np.percentile(values,99.5)) if len(values) else 1
                im=ax.imshow(a,extent=(0,width,0,height),cmap='viridis',vmin=0,vmax=max(vmax,1),interpolation='nearest')
            region=(source_info or {}).get('place',{}).get('name','')
            if binary and scale<1:title='Corridor coverage'
            ax.set_title((region+' · ' if region else '')+title+' | wind from '+p['wind_from']);ax.set_xlabel('Easting offset (m)');ax.set_ylabel('Northing offset (m)')
            fig.colorbar(im,ax=ax,label=('Corridor fraction within display pixel' if scale<1 else '0: other; 1: corridor') if binary else 'Path count (dimensionless)')
            fig.savefig(output/(name+'.png'),dpi=180);plt.close(fig)
        lines.extend(['','![平均累积](acc_mean.png)','','![风道筛选](corridor_otsu.png)'])
        lines.extend(['','大范围风道预览按显示像元内廊道覆盖比例着色；原始GeoTIFF仍为实际计算网格上的二值结果，面积由原始网格统计。'])
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf8')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--no-plot',action='store_true');args=ap.parse_args()
    render(args.out,not args.no_plot)
