#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPS緯度経度から各カテゴリの近い施設 TOP5 を表示するスクリプト

使用方法:
    python nearest_pois.py <緯度> <経度>
    python nearest_pois.py          # 引数なしで対話入力
    例: python nearest_pois.py 35.6812 139.7671

データソース: japan_pois.py (extract_japan_pois.py で生成)
"""

import sys
import math
import unicodedata

try:
    import japan_pois
except ImportError:
    print("エラー: japan_pois.py が見つかりません。")
    print("先に extract_japan_pois.py を実行してデータを生成してください。")
    print("  例: python extract_japan_pois.py japan.osm.pbf japan_pois --format python")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 距離計算
# ---------------------------------------------------------------------------

def _name_key(name: str) -> str:
    """重複判定用キー: 空白除去 + NFKC正規化（全角→半角）+ 小文字化。"""
    return "".join(unicodedata.normalize("NFKC", name).split()).lower()


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2点間の距離をメートルで返す（ハーバーサイン公式）"""
    R = 6_371_000  # 地球半径 (m)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(min(a, 1.0)))


def top5_nearest(pois: list, lat: float, lon: float, dedup_radius: float = 3000.0) -> list:
    """POIリストから近い順に top5 を返す（距離メートル付き）。
    同じ名前かつ dedup_radius (m) 以内の重複エントリは1件に絞る。"""
    with_dist = [
        (haversine(lat, lon, p["lat"], p["lon"]), p)
        for p in pois
        if p.get("lat") is not None and p.get("lon") is not None
        and (p.get("name") or "").strip()
    ]
    with_dist.sort(key=lambda x: x[0])

    results: list = []
    for dist, poi in with_dist:
        name = poi.get("name") or ""
        name_key = _name_key(name)
        # 同名（空白・全半角無視）かつ近距離のエントリは重複とみなしてスキップ
        if name_key and any(
            _name_key(existing.get("name") or "") == name_key
            and haversine(poi["lat"], poi["lon"], existing["lat"], existing["lon"]) < dedup_radius
            for _, existing in results
        ):
            continue
        results.append((dist, poi))
        if len(results) >= 5:
            break
    return results


# ---------------------------------------------------------------------------
# 高速道路施設の名称分類
# ---------------------------------------------------------------------------

# キーワード（具体的・長いものを先に並べる）
_SA_KEYWORDS  = ["サービスエリア", "ＳＡ", "SA"]
_PA_KEYWORDS  = ["パーキングエリア", "ＰＡ", "PA"]
_JCT_KEYWORDS = ["ジャンクション", "ＪＣＴ", "JCT"]
_IC_KEYWORDS  = ["インターチェンジ", "ＩＣ", "IC"]

# 表示正規化マッピング（長いものを先に）
_NORMALIZE_MAP = [
    ("ジャンクション", "JCT"),
    ("インターチェンジ", "IC"),
    ("サービスエリア", "SA"),
    ("パーキングエリア", "PA"),
    ("ＪＣＴ", "JCT"),
    ("ＩＣ",  "IC"),
    ("ＳＡ",  "SA"),
    ("ＰＡ",  "PA"),
]


def normalize_name(name: str) -> str:
    """表示用：全角・カタカナ表記を半角英字に正規化する。"""
    for src, dst in _NORMALIZE_MAP:
        name = name.replace(src, dst)
    return name


def classify_highway(name: str) -> str:
    """
    名称文字列から高速道路施設カテゴリを判定する。
    複数カテゴリが含まれる場合は '/' 区切りで返す（例: 'IC/PA'）。
    道の駅 → 'michi_no_eki'、判定不能 → 'services'
    """
    if "道の駅" in name:
        return "michi_no_eki"

    cats: list[str] = []
    # 優先度順: JCT > IC > SA > PA
    if any(kw in name for kw in _JCT_KEYWORDS):
        cats.append("JCT")
    if any(kw in name for kw in _IC_KEYWORDS):
        cats.append("IC")
    if any(kw in name for kw in _SA_KEYWORDS):
        cats.append("SA")
    if any(kw in name for kw in _PA_KEYWORDS):
        cats.append("PA")

    return "/".join(cats) if cats else "services"


def build_highway_pois(
    michi_no_eki: list, junctions: list, services: list
) -> tuple[list, list, list]:
    """
    michi_no_eki / junctions / services を統合し名称で再分類する。
    名称から判定できない場合は元データの category をフォールバックとして使用し、
    さらに名前にカテゴリ略称を補完する（例: "京橋" → "京橋IC"）。
    Returns: (道の駅リスト, IC/JCTリスト, SA/PAリスト)
    """
    # 名前に付与する略称（フォールバック時のみ）
    _SUFFIX: dict[str, str] = {"IC": "IC", "JCT": "JCT", "SA": "SA", "PA": "PA"}

    michi: list = []
    ic_jct: list = []
    sa_pa:  list = []

    sources = [
        (michi_no_eki, "michi_no_eki"),
        (junctions,    None),       # 元 category (IC/JCT) を優先
        (services,     None),       # 元 category (SA/PA 等) を優先
    ]

    for source, source_default in sources:
        for p in source:
            name = p.get("name") or ""
            cat = classify_highway(name)

            if cat == "services":
                # フォールバック: 元データの category → source_default の順
                fallback = p.get("category") or source_default or "services"
                if fallback != "services" and fallback != "michi_no_eki":
                    # 名前にカテゴリ略称が含まれていなければ補完
                    suffix = _SUFFIX.get(fallback, "")
                    if suffix and suffix not in normalize_name(name):
                        name = name + suffix
                cat = fallback

            poi = {**p, "name": name, "category": cat}

            if cat == "michi_no_eki":
                michi.append(poi)
            elif "JCT" in cat or "IC" in cat:
                ic_jct.append(poi)
            else:
                sa_pa.append(poi)

    return michi, ic_jct, sa_pa


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------

_CAT_LABEL: dict[str, str] = {
    "station":      "鉄道駅",
    "michi_no_eki": "道の駅",
    "services":     "高速施設",
    "shrine":       "神社",
    "temple":       "寺院",
    "cape":         "岬",
    "viewpoint":    "展望地",
    "waterfall":    "滝",
    "hot_spring":   "温泉",
    "beach":        "海岸",
    "cliff":        "断崖",
    "mountain":     "山",
    "harbour":      "港",
    "lighthouse":   "灯台",
    "pass":         "峠",
    # SA/PA/IC/JCT および組み合わせ (IC/PA 等) はそのまま表示
}


def format_distance(meters: float) -> str:
    if meters < 1000:
        return f"{meters:.0f}m"
    return f"{meters / 1000:.1f}km"


def print_section(title: str, items: list) -> None:
    print(f"\n{'=' * 52}")
    print(f"  {title}")
    print(f"{'=' * 52}")
    if not items:
        print("  データなし")
        return
    for rank, (dist, poi) in enumerate(items, 1):
        name = normalize_name(poi.get("name") or "(名称不明)")
        cat = poi.get("category", "")
        # 駅名に「駅」「停留場」「停車場」が付いていなければ補完
        if cat == "station" and name != "(名称不明)" and not name.endswith(("駅", "停留場", "停車場")):
            name += "駅"
        cat_label = _CAT_LABEL.get(cat, cat)  # 未登録は cat 値をそのまま使用
        label_str = f" [{cat_label}]" if cat_label else ""
        extra = ""
        if poi.get("operator"):
            extra = f"  ({poi['operator']})"
        elif cat == "mountain" and poi.get("ele"):
            extra = f"  (標高 {poi['ele']}m)"
        print(f"  {rank}. {name}{label_str}  {format_distance(dist)}{extra}")
        print(f"     緯度 {poi['lat']:.6f}, 経度 {poi['lon']:.6f}")


# ---------------------------------------------------------------------------
# 入力パース
# ---------------------------------------------------------------------------

def parse_coords(lat_str: str, lon_str: str) -> tuple[float, float]:
    try:
        lat = float(lat_str.strip())
        lon = float(lon_str.strip())
    except ValueError:
        raise ValueError("緯度・経度は数値で入力してください。")
    if not (-90 <= lat <= 90):
        raise ValueError(f"緯度は -90〜90 の範囲で入力してください。(入力値: {lat})")
    if not (-180 <= lon <= 180):
        raise ValueError(f"経度は -180〜180 の範囲で入力してください。(入力値: {lon})")
    return lat, lon


# ---------------------------------------------------------------------------
# データ読み込み
# ---------------------------------------------------------------------------

def load_pois() -> dict:
    stations        = getattr(japan_pois, "stations",        [])
    michi_no_eki    = getattr(japan_pois, "michi_no_eki",    [])
    junctions       = getattr(japan_pois, "junctions",       [])
    services        = getattr(japan_pois, "services",        [])
    shrines_temples = getattr(japan_pois, "shrines_temples", [])
    scenic_spots    = getattr(japan_pois, "scenic_spots",    [])
    mountains       = getattr(japan_pois, "mountains",       [])
    ports_etc       = getattr(japan_pois, "ports_etc",       [])

    total = len(stations) + len(michi_no_eki) + len(junctions) + len(services)
    if total == 0:
        print("警告: japan_pois.py にデータが含まれていません。")
        print("先に extract_japan_pois.py でデータを生成してください。")
        print("  例: python extract_japan_pois.py japan.osm.pbf japan_pois --format python")

    return {
        "stations":        stations,
        "michi_no_eki":    michi_no_eki,
        "junctions":       junctions,
        "services":        services,
        "shrines_temples": shrines_temples,
        "scenic_spots":    scenic_spots,
        "mountains":       mountains,
        "ports_etc":       ports_etc,
    }


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def run(lat: float, lon: float, pois: dict) -> None:
    michi, ic_jct, sa_pa = build_highway_pois(
        pois["michi_no_eki"], pois["junctions"], pois["services"]
    )
    print(f"\n検索座標: 緯度 {lat}, 経度 {lon}")
    print_section("鉄道駅      TOP5", top5_nearest(pois["stations"],        lat, lon))
    print_section("道の駅      TOP5", top5_nearest(michi,                   lat, lon))
    print_section("IC/JCT      TOP5", top5_nearest(ic_jct,                  lat, lon))
    print_section("SA/PA       TOP5", top5_nearest(sa_pa,                   lat, lon))
    print_section("神社仏閣    TOP5", top5_nearest(pois["shrines_temples"],  lat, lon))
    print_section("景勝地      TOP5", top5_nearest(pois["scenic_spots"],     lat, lon))
    print_section("山          TOP5", top5_nearest(pois["mountains"],        lat, lon))
    print_section("港・灯台・峠 TOP5", top5_nearest(pois["ports_etc"],       lat, lon))
    print()
    print("Data © OpenStreetMap contributors, ODbL 1.0"
          " | https://www.openstreetmap.org/copyright")


def main() -> None:
    pois = load_pois()

    # --- コマンドライン引数モード ---
    if len(sys.argv) == 3:
        try:
            lat, lon = parse_coords(sys.argv[1], sys.argv[2])
        except ValueError as e:
            print(f"エラー: {e}")
            sys.exit(1)
        run(lat, lon, pois)
        return

    if len(sys.argv) != 1:
        print(f"使用方法: python {sys.argv[0]} <緯度> <経度>")
        print(f"  例:     python {sys.argv[0]} 35.6812 139.7671")
        sys.exit(1)

    # --- 対話入力モード ---
    print("近隣施設検索（Ctrl+C または 空Enter で終了）")
    while True:
        print()
        try:
            raw = input("緯度 経度を入力（例: 35.6812 139.7671）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n終了します。")
            break

        if not raw:
            break

        parts = raw.split()
        if len(parts) != 2:
            print("エラー: 「緯度 経度」の形式でスペース区切りで入力してください。")
            continue

        try:
            lat, lon = parse_coords(parts[0], parts[1])
        except ValueError as e:
            print(f"エラー: {e}")
            continue

        run(lat, lon, pois)


if __name__ == "__main__":
    main()
