"""地名 -> 行政边界（WGS84）。

数据源：阿里云 DataV GeoAtlas（免密钥、国内可达、约 30 KB/市）。
注意：DataV 的 areas_v3 系列是 **GCJ-02（火星坐标）**，与 WGS84 相差约 400-600 m。
本模块默认将其纠正为 WGS84，否则会与 DEM / CNBH（均为 WGS84）整体错位。
"""

from __future__ import annotations

import json
import math
import os
import time

from contextvars import ContextVar
from .network import request,atomic_json

_PROXY=ContextVar('boundary_proxy',default=None)

DATA_V = "https://geo.datav.aliyun.com/areas_v3/bound"

# 34 个省级行政区 adcode（GB/T 2260），用于未知城市时下钻查找
PROVINCES = {
    110000: "北京", 120000: "天津", 130000: "河北", 140000: "山西", 150000: "内蒙古",
    210000: "辽宁", 220000: "吉林", 230000: "黑龙江", 310000: "上海", 320000: "江苏",
    330000: "浙江", 340000: "安徽", 350000: "福建", 360000: "江西", 370000: "山东",
    410000: "河南", 420000: "湖北", 430000: "湖南", 440000: "广东", 450000: "广西",
    460000: "海南", 500000: "重庆", 510000: "四川", 520000: "贵州", 530000: "云南",
    540000: "西藏", 610000: "陕西", 620000: "甘肃", 630000: "青海", 640000: "宁夏",
    650000: "新疆", 710000: "台湾", 810000: "香港", 820000: "澳门",
}

# 直辖市：市级边界直接用省级 adcode
MUNICIPALITIES = {110000, 120000, 310000, 500000}

# 常用城市快捷键，避免首次扫描 34 个省（约 5 MB / 10 s）
COMMON_CITIES = {
    "北京": 110000, "上海": 310000, "天津": 120000, "重庆": 500000,
    "武汉": 420100, "广州": 440100, "深圳": 440300, "成都": 510100,
    "杭州": 330100, "南京": 320100, "西安": 610100, "郑州": 410100,
    "长沙": 430100, "合肥": 340100, "南昌": 360100, "济南": 370100,
    "青岛": 370200, "沈阳": 210100, "大连": 210200, "哈尔滨": 230100,
    "长春": 220100, "石家庄": 130100, "太原": 140100, "呼和浩特": 150100,
    "福州": 350100, "厦门": 350200, "南宁": 450100, "海口": 460100,
    "贵阳": 520100, "昆明": 530100, "拉萨": 540100, "兰州": 620100,
    "西宁": 630100, "银川": 640100, "乌鲁木齐": 650100, "苏州": 320500,
    "宁波": 330200, "无锡": 320200, "佛山": 440600, "东莞": 441900,
}

_SUFFIXES = ("特别行政区", "自治区", "自治州", "地区", "新区", "市", "省", "县", "区")


def normalize(name: str) -> str:
    """'武汉市' / '武汉' / '湖北省' -> '武汉'。"""
    s = str(name).strip().replace(" ", "")
    for suf in _SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


# ---------------------------------------------------------------- GCJ-02 纠偏
_A = 6378245.0
_EE = 0.00669342162296594323


def _tlat(x, y):
    r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    r += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return r


def _tlon(x, y):
    r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    r += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return r


def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    """单点 GCJ-02 -> WGS84（近似反解，误差 < 1 m）。"""
    if not (73.5 < lon < 135.1 and 18.1 < lat < 53.6):   # 境外不偏移
        return lon, lat
    dlat = _tlat(lon - 105.0, lat - 35.0)
    dlon = _tlon(lon - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    magic = 1 - _EE * math.sin(rad) ** 2
    sq = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sq) * math.pi)
    dlon = (dlon * 180.0) / (_A / sq * math.cos(rad) * math.pi)
    return lon - dlon, lat - dlat


# ---------------------------------------------------------------- HTTP
def _get_json(url: str, retries: int = 3, timeout: int = 40):
    last = None
    for i in range(retries):
        try:
            with request(url,_PROXY.get(),timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:      # noqa: BLE001
            last = e
            time.sleep(1.0 + i)
    raise RuntimeError(f"请求失败 {url}: {last}")


# ---------------------------------------------------------------- 索引
def _index_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "city_index.json")


def _load_index(cache_dir: str) -> dict:
    p = _index_path(cache_dir)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return dict(COMMON_CITIES,**json.load(f))
        except Exception:       # noqa: BLE001
            return dict(COMMON_CITIES)
    return dict(COMMON_CITIES)


def _save_index(cache_dir: str, idx: dict) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    atomic_json(_index_path(cache_dir),idx)


def build_city_index(cache_dir: str, log=print) -> dict:
    """扫描 34 个省，建立 城市名 -> adcode 索引（首次约 10 s，之后走缓存）。"""
    idx = dict(COMMON_CITIES)
    for prov, pname in PROVINCES.items():
        try:
            d = _get_json(f"{DATA_V}/{prov}_full.json")
        except Exception as e:      # noqa: BLE001
            log(f"  [warn] 跳过 {pname}: {e}")
            continue
        for feat in d.get("features", []):
            p = feat.get("properties", {})
            nm, ad = p.get("name"), p.get("adcode")
            if not nm or not ad:
                continue
            if prov in MUNICIPALITIES and p.get("level") == "district":
                continue            # 直辖市不把区当城市
            idx.setdefault(normalize(nm), int(ad))
    _save_index(cache_dir, idx)
    log(f"  城市索引已建立：{len(idx)} 条")
    return idx


def _find_district(city_adcode: int, name_part: str, cache_dir: str, log=print):
    """在市 _full.json 里找区县。返回 adcode 或 None。"""
    cache = os.path.join(cache_dir, f"districts_{city_adcode}.json") if cache_dir else ""
    mapping = None
    if cache and os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                mapping = json.load(f)
        except Exception:       # noqa: BLE001
            mapping = None
    if mapping is None:
        mapping = {}
        try:
            d = _get_json(f"{DATA_V}/{city_adcode}_full.json")
            for feat in d.get("features", []):
                p = feat.get("properties", {})
                nm, ad = p.get("name"), p.get("adcode")
                if nm and ad:
                    mapping[normalize(nm)] = int(ad)
            if cache:
                os.makedirs(cache_dir, exist_ok=True)
                atomic_json(cache,mapping)
        except Exception as e:      # noqa: BLE001
            log(f"  [warn] 取 {city_adcode} 的区县列表失败：{e}")
            return None
    if name_part in mapping:
        return mapping[name_part]
    return None


def find_adcode(name: str, cache_dir: str, log=print) -> tuple[int, str]:
    key = normalize(name)
    idx = _load_index(cache_dir)
    def lookup(index):
        if key in index:return int(index[key]),key
        for city_key,ad in sorted(index.items(),key=lambda item:len(item[0]),reverse=True):
            if len(city_key)>=2 and key.startswith(city_key) and len(key)>len(city_key):
                rest=key[len(city_key):]
                if rest.startswith('市'):rest=rest[1:]
                sub=_find_district(int(ad),rest,cache_dir,log=log)
                if sub:return sub,f'{city_key}{rest}'
                raise ValueError(f'无法核实{name}的区县边界，不能替换为整个{city_key}市')
        return None
    found=lookup(idx)
    if found:return found

    log(f"  未在索引中命中「{name}」，扫描全国地级市清单…")
    idx = build_city_index(cache_dir, log=log)
    found=lookup(idx)
    if found:return found
    raise ValueError(
        f"无法识别地名「{name}」。带区县时请加市名（如「武汉市江岸区」）；"
        f"或直接用市名，或给定 bbox。当前已索引 {len(idx)} 个地名。"
    )


# ---------------------------------------------------------------- 边界
def _iter_rings(geom):
    t, c = geom["type"], geom["coordinates"]
    if t == "Polygon":
        for ring in c:
            yield ring
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                yield ring
    else:
        raise ValueError(f"不支持的几何类型 {t}")


def fetch_boundary(adcode: int, cache_dir: str = "", fix_gcj: bool = True) -> dict:
    """返回 {'name','adcode','geometry','bbox_wgs84','n_rings','n_vertices'}。"""
    cache_file = ""
    if cache_dir:
        cache_file = os.path.join(cache_dir, f"bound_{adcode}_{'wgs84' if fix_gcj else 'gcj02'}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:return json.load(f)
            except (OSError,ValueError):pass

    d = _get_json(f"{DATA_V}/{adcode}.json")
    feats = d.get("features", [])
    if not feats:
        raise RuntimeError(f"adcode {adcode} 未返回边界")
    f0 = feats[0]
    props = f0.get("properties", {})
    geom = f0["geometry"]

    if fix_gcj:
        geom = {
            "type": geom["type"],
            "coordinates": [
                [[list(gcj02_to_wgs84(x, y)) for x, y in ring]
                 for ring in poly]
                if geom["type"] == "MultiPolygon" else
                [list(gcj02_to_wgs84(x, y)) for x, y in poly]
                for poly in geom["coordinates"]
            ],
        }

    xs, ys, nv = [], [], 0
    for ring in _iter_rings(geom):
        for x, y in ring:
            xs.append(x)
            ys.append(y)
        nv += len(ring)

    out = {
        "name": props.get("name", str(adcode)),
        "adcode": int(props.get("adcode", adcode)),
        "level": props.get("level", ""),
        "geometry": geom,
        "bbox_wgs84": [min(xs), min(ys), max(xs), max(ys)],
        "n_rings": sum(1 for _ in _iter_rings(geom)),
        "n_vertices": nv,
        "coord_fix": "GCJ-02 -> WGS84" if fix_gcj else "原样（GCJ-02）",
    }
    if cache_file:
        os.makedirs(cache_dir, exist_ok=True)
        atomic_json(cache_file,out)
    return out


def resolve(name: str, cache_dir: str = "", fix_gcj: bool = True, log=print,proxy=None) -> dict:
    """地名 -> 边界 + bbox（WGS84）。"""
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    token=_PROXY.set(proxy)
    try:
        ad,matched=find_adcode(name,cache_dir,log=log)
        b=fetch_boundary(ad,cache_dir=cache_dir,fix_gcj=fix_gcj)
    finally:_PROXY.reset(token)
    b["query"] = name
    b["matched"] = matched
    log(f"  地名解析：「{name}」→ {b['name']}（adcode {ad}），"
        f"bbox {b['bbox_wgs84'][0]:.4f},{b['bbox_wgs84'][1]:.4f} – "
        f"{b['bbox_wgs84'][2]:.4f},{b['bbox_wgs84'][3]:.4f}，"
        f"{b['n_rings']} 个多边形 / {b['n_vertices']} 个顶点")
    return b
