# Geospatial Data Processing

## Core Concepts
- Coordinate Reference Systems (WGS84, UTM)
- Vector vs Raster data
- Common file formats (Shapefile, GeoJSON, GeoTIFF)

## Python Tools
```python
import geopandas as gpd
import rasterio
from shapely.geometry import Point

# Example: Reading shapefile
gdf = gpd.read_file('data.shp')

# Example: Coordinate transformation
gdf = gdf.to_crs('EPSG:4326')  # WGS84

# Example: Point in polygon analysis
point = Point(-122.3, 47.6)
polygon = gdf.geometry[0]
point.within(polygon)
```