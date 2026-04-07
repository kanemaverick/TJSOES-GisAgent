from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, Polygon


def main() -> None:
    base = Path(__file__).resolve().parent / "data"
    base.mkdir(parents=True, exist_ok=True)

    polygons = gpd.GeoDataFrame(
        {
            "name": ["A", "B", "C", "D"],
            "value": [10, 25, 18, 32],
        },
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            Polygon([(0, 1), (1, 1), (1, 2), (0, 2)]),
            Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
        ],
        crs="EPSG:4326",
    )
    polygons.to_file(base / "sample_polygons.geojson", driver="GeoJSON")

    points = gpd.GeoDataFrame(
        {"label": ["P1", "P2", "P3"], "score": [3, 8, 5]},
        geometry=[Point(0.4, 0.4), Point(1.5, 0.7), Point(1.2, 1.7)],
        crs="EPSG:4326",
    )
    points.to_file(base / "sample_points.geojson", driver="GeoJSON")
    print(f"Sample data written to {base}")


if __name__ == "__main__":
    main()
