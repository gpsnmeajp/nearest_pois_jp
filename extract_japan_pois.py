#!/usr/bin/env python3
"""
日本全国 POI 抽出スクリプト (osmium-tool / pyosmium 使用)

対象施設:
    - 鉄道駅          railway=station
    - 道の駅          highway=rest_area / amenity=rest_area  (名称に "道の駅" を含む)
    - 高速IC/JCT      highway=motorway_junction
    - 高速SA/PA       highway=services

データソース (Geofabrik):
    https://download.geofabrik.de/asia/japan-latest.osm.pbf

使用方法:
    python extract_japan_pois.py japan-latest.osm.pbf
    python extract_japan_pois.py japan-latest.osm.pbf output.json
    python extract_japan_pois.py japan-latest.osm.pbf --format csv

ノードロケーションキャッシュ:
    大容量ファイル(日本全土 ~2GB)の処理では、メモリ不足を防ぐため
    ディスクキャッシュを使用します（--cache オプション参照）。
"""

import sys
import json
import csv
import argparse
import logging
from pathlib import Path

import osmium

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

def _make_point(name: str, lat: float, lon: float, category: str, **extra) -> dict:
    entry = {
        "name": name,
        "lat": round(lat, 7),
        "lon": round(lon, 7),
        "category": category,
    }
    entry.update(extra)
    return entry


# ---------------------------------------------------------------------------
# osmium ハンドラ
# ---------------------------------------------------------------------------

class JapanPoiHandler(osmium.SimpleHandler):
    """
    OSM ファイルをストリーム処理し、対象施設を抽出する。

    結果は以下の属性に格納される:
        stations        : list[dict]  鉄道駅
        michi_no_eki    : list[dict]  道の駅
        junctions       : list[dict]  高速IC/JCT
        services        : list[dict]  高速SA/PA
        shrines_temples : list[dict]  神社仏閣
        scenic_spots    : list[dict]  景勝地 (岬・展望地・滝・温泉・海岸・断崖)
        mountains       : list[dict]  山 (標高200m以上)
        ports_etc       : list[dict]  港・灯台・峠
    """

    def __init__(self) -> None:
        super().__init__()
        self.stations: list[dict] = []
        self.michi_no_eki: list[dict] = []
        self.junctions: list[dict] = []
        self.services: list[dict] = []
        self.shrines_temples: list[dict] = []
        self.scenic_spots: list[dict] = []
        self.mountains: list[dict] = []
        self.ports_etc: list[dict] = []

        # エリア重複排除用セット (osmIDベース)
        self._seen_area_ids: set[int] = set()

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _name(tags) -> str:
        """日本語名 → 英語名 → ref の優先順位で名称を返す。"""
        return (
            tags.get("name:ja")
            or tags.get("name")
            or tags.get("ref")
            or ""
        )

    @staticmethod
    def _junction_category(name: str, tags) -> str:
        n = name.upper()
        if "JCT" in n or "ジャンクション" in n:
            return "JCT"
        ref = tags.get("ref", "")
        if ref.endswith("JCT"):
            return "JCT"
        return "IC"

    @staticmethod
    def _service_category(name: str) -> str:
        n = name.upper()
        if "サービスエリア" in name or " SA" in n or n.endswith("SA"):
            return "SA"
        if "パーキングエリア" in name or " PA" in n or n.endswith("PA"):
            return "PA"
        return "services"

    # ------------------------------------------------------------------
    # ノード処理 (node callback)
    # ------------------------------------------------------------------

    def node(self, n) -> None:
        if not n.location.valid():
            return

        tags = n.tags
        lat = n.location.lat
        lon = n.location.lon

        # 鉄道駅
        if tags.get("railway") == "station":
            self.stations.append(
                _make_point(
                    self._name(tags), lat, lon, "station",
                    name_en=tags.get("name:en", ""),
                    operator=tags.get("operator", ""),
                    station_type=tags.get("station", ""),
                )
            )

        # 高速IC/JCT
        if tags.get("highway") == "motorway_junction":
            name = self._name(tags)
            self.junctions.append(
                _make_point(
                    name, lat, lon,
                    self._junction_category(name, tags),
                    ref=tags.get("ref", ""),
                )
            )

        highway = tags.get("highway", "")
        amenity = tags.get("amenity", "")
        name = self._name(tags)

        # 道の駅 (ノード)
        if highway in ("rest_area",) or amenity in ("rest_area",):
            if "道の駅" in name:
                self.michi_no_eki.append(
                    _make_point(name, lat, lon, "michi_no_eki")
                )

        # SA/PA (ノード)
        if highway == "services":
            self.services.append(
                _make_point(name, lat, lon, self._service_category(name))
            )

        natural = tags.get("natural", "")
        tourism = tags.get("tourism", "")
        religion = tags.get("religion", "")
        man_made = tags.get("man_made", "")

        # 神社仏閣 (ノード)
        if amenity == "place_of_worship":
            if religion == "shinto":
                self.shrines_temples.append(
                    _make_point(name, lat, lon, "shrine")
                )
            elif religion == "buddhist":
                self.shrines_temples.append(
                    _make_point(name, lat, lon, "temple")
                )

        # 景勝地 (ノード: 岬・展望地・滝・温泉・海岸・断崖)
        if natural == "cape":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "cape")
            )
        elif tourism == "viewpoint":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "viewpoint")
            )
        elif natural == "waterfall":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "waterfall")
            )
        elif natural == "hot_spring":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "hot_spring")
            )
        elif natural == "beach":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "beach")
            )
        elif natural == "cliff":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "cliff")
            )

        # 山 (ノード: 標高200m以上の山頂)
        if natural == "peak":
            try:
                ele = float(tags.get("ele") or 0)
            except (ValueError, TypeError):
                ele = 0.0
            if ele >= 200:
                self.mountains.append(
                    _make_point(name, lat, lon, "mountain", ele=round(ele, 1))
                )

        # 港 (ノード)
        if (amenity == "ferry_terminal"
                or tags.get("harbour") == "yes"
                or tags.get("landuse") == "harbour"):
            self.ports_etc.append(
                _make_point(name, lat, lon, "harbour")
            )

        # 灯台 (ノード)
        if man_made == "lighthouse":
            self.ports_etc.append(
                _make_point(name, lat, lon, "lighthouse")
            )

        # 峠 (ノード)
        if tags.get("mountain_pass") == "yes" or natural == "saddle":
            self.ports_etc.append(
                _make_point(name, lat, lon, "pass")
            )

    # ------------------------------------------------------------------
    # エリア処理 (area callback)
    # エリア(閉じたway・multipolygon)の中心点を近似的に算出する。
    # ------------------------------------------------------------------

    def area(self, a) -> None:
        # 同一オブジェクトの重複登録を防ぐ
        if a.id in self._seen_area_ids:
            return
        self._seen_area_ids.add(a.id)

        # 外周リングのノード座標から重心を算出する
        lats: list[float] = []
        lons: list[float] = []
        for outer in a.outer_rings():
            for node in outer:
                if node.location.valid():
                    lats.append(node.location.lat)
                    lons.append(node.location.lon)

        if not lats:
            return

        lat = sum(lats) / len(lats)
        lon = sum(lons) / len(lons)

        tags = a.tags
        highway = tags.get("highway", "")
        amenity = tags.get("amenity", "")
        name = self._name(tags)

        # 道の駅 (エリア)
        if highway in ("rest_area",) or amenity in ("rest_area",):
            if "道の駅" in name:
                self.michi_no_eki.append(
                    _make_point(name, lat, lon, "michi_no_eki")
                )

        # SA/PA (エリア)
        if highway == "services":
            self.services.append(
                _make_point(name, lat, lon, self._service_category(name))
            )

        # 駅舎エリア
        if tags.get("railway") == "station":
            self.stations.append(
                _make_point(
                    name, lat, lon, "station",
                    name_en=tags.get("name:en", ""),
                    operator=tags.get("operator", ""),
                    station_type=tags.get("station", ""),
                )
            )

        natural = tags.get("natural", "")
        tourism = tags.get("tourism", "")
        religion = tags.get("religion", "")

        # 神社仏閣 (エリア)
        if amenity == "place_of_worship":
            if religion == "shinto":
                self.shrines_temples.append(
                    _make_point(name, lat, lon, "shrine")
                )
            elif religion == "buddhist":
                self.shrines_temples.append(
                    _make_point(name, lat, lon, "temple")
                )

        # 景勝地 (エリア: 岬・展望地・滝・温泉・海岸・断崖)
        if natural == "cape":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "cape")
            )
        elif tourism == "viewpoint":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "viewpoint")
            )
        elif natural == "waterfall":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "waterfall")
            )
        elif natural == "hot_spring":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "hot_spring")
            )
        elif natural == "beach":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "beach")
            )
        elif natural == "cliff":
            self.scenic_spots.append(
                _make_point(name, lat, lon, "cliff")
            )

        # 港 (エリア)
        if (amenity == "ferry_terminal"
                or tags.get("harbour") == "yes"
                or tags.get("landuse") == "harbour"):
            self.ports_etc.append(
                _make_point(name, lat, lon, "harbour")
            )


# ---------------------------------------------------------------------------
# 主処理
# ---------------------------------------------------------------------------

def extract_pois(osm_file: str, idx: str = "flex_mem") -> dict:
    """
    OSM ファイルを処理し、施設ごとのリストを返す。

    Parameters
    ----------
    osm_file : str
        入力 OSM/PBF ファイルパス
    idx : str
        ノードロケーションストレージの種別。
        "flex_mem"                         : メモリ (RAM ~128GB 必要)
        "dense_file_array,/tmp/node.cache" : ディスク (ディスク ~128GB 必要)
        "sparse_file_array,/tmp/node.cache": スパースディスク (部分データ向け)

    Returns
    -------
    dict
        {
          "stations"     : [{"name", "lat", "lon", "category", ...}, ...],
          "michi_no_eki" : [...],
          "junctions"    : [...],   # IC / JCT
          "services"     : [...],   # SA / PA
        }
    """
    log.info("処理開始: %s", osm_file)
    log.info("ロケーションストア: %s", idx)

    handler = JapanPoiHandler()
    handler.apply_file(osm_file, locations=True, idx=idx)

    result = {
        "stations":        handler.stations,
        "michi_no_eki":    handler.michi_no_eki,
        "junctions":       handler.junctions,
        "services":        handler.services,
        "shrines_temples": handler.shrines_temples,
        "scenic_spots":    handler.scenic_spots,
        "mountains":       handler.mountains,
        "ports_etc":       handler.ports_etc,
    }

    log.info("鉄道駅      : %6d 件", len(result["stations"]))
    log.info("道の駅      : %6d 件", len(result["michi_no_eki"]))
    log.info("IC / JCT    : %6d 件", len(result["junctions"]))
    log.info("SA / PA     : %6d 件", len(result["services"]))
    log.info("神社仏閣    : %6d 件", len(result["shrines_temples"]))
    log.info("景勝地      : %6d 件", len(result["scenic_spots"]))
    log.info("山 (≥200m) : %6d 件", len(result["mountains"]))
    log.info("港・灯台・峠: %6d 件", len(result["ports_etc"]))

    return result


# ---------------------------------------------------------------------------
# 出力ヘルパー
# ---------------------------------------------------------------------------

def write_json(result: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info("JSON 出力: %s", path)


def write_csv(result: dict, path: str) -> None:
    all_rows: list[dict] = []
    for items in result.values():
        all_rows.extend(items)

    if not all_rows:
        log.warning("出力データがありません。")
        return

    fieldnames = list(all_rows[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    log.info("CSV 出力: %s", path)


def write_python(result: dict, path: str) -> None:
    """Python ソースファイルとして出力 (直接 import 可能)。"""
    lines = [
        "# -*- coding: utf-8 -*-",
        "# このファイルは自動生成されました",
        "#",
        "# Data © OpenStreetMap contributors, ODbL 1.0",
        "# https://www.openstreetmap.org/copyright",
        "# https://opendatacommons.org/licenses/odbl/1-0/",
        "",
    ]
    for key, items in result.items():
        lines.append(f"{key} = {repr(items)}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("Python データファイル出力: %s", path)


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OSM PBF から日本の施設位置情報を抽出します。"
    )
    parser.add_argument("osm_file", help="入力 OSM/PBF ファイル")
    parser.add_argument(
        "output", nargs="?", default="japan_pois.json",
        help="出力ファイル (デフォルト: japan_pois.json)",
    )
    parser.add_argument(
        "--format", choices=["json", "csv", "python"], default="json",
        help="出力形式 (デフォルト: json)",
    )
    parser.add_argument(
        "--cache",
        default=None,
        metavar="PATH",
        help=(
            "ノードロケーションのディスクキャッシュパス。"
            "未指定の場合はメモリを使用 (RAM ~128GB 必要)。"
            "例: --cache /tmp/node.cache"
        ),
    )
    args = parser.parse_args()

    if not Path(args.osm_file).exists():
        log.error("ファイルが見つかりません: %s", args.osm_file)
        sys.exit(1)

    # ロケーションストア選択
    if args.cache:
        idx = f"dense_file_array,{args.cache}"
    else:
        idx = "flex_mem"

    result = extract_pois(args.osm_file, idx=idx)

    fmt = args.format
    out = args.output

    if fmt == "json":
        if not out.endswith(".json"):
            out = Path(out).stem + ".json"
        write_json(result, out)
    elif fmt == "csv":
        if not out.endswith(".csv"):
            out = Path(out).stem + ".csv"
        write_csv(result, out)
    elif fmt == "python":
        if not out.endswith(".py"):
            out = Path(out).stem + ".py"
        write_python(result, out)

    log.info("Data © OpenStreetMap contributors, ODbL 1.0 "
             "| https://www.openstreetmap.org/copyright")


if __name__ == "__main__":
    main()
