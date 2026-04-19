# Geographic Information System (GIS) Workflows

## Current Gap
- Team has extensive ecological scenario modeling and urban ecology skills but NO geospatial tools
- Cannot process spatial data, maps, or location-based analysis

## Required Capabilities
1. **Vector Data Processing** (GeoJSON, Shapefiles)
   - Use `geopandas` in Docker sandbox
   - Parse spatial coordinates, geometries, attributes
   - Spatial joins and overlays
2. **Raster Data Analysis** (satellite imagery, elevation)
   - Use `rasterio` and `rioxarray`
   - NDVI, land cover classification
3. **Coordinate Reference Systems (CRS)**
   - Reprojection: EPSG conversions
   - Distance/area calculations in appropriate units
4. **Spatial Visualization**
   - Generate static maps with `matplotlib`/`contextily`
   - Interactive maps via `folium`
5. **Integrations**
   - Link spatial data to rapid ecological impact forecasting
   - Urban ecology hotspot identification
   - Stakeholder location mapping for ecological engagement

## MCP Servers to Add
- Search for: 'openstreetmap', 'geojson', 'mapbox', 'google_maps'
- Expected benefits: geocoding, reverse geocoding, tile layers

## Quick Wins
- Convert address lists to coordinates for stakeholder mapping
- Overlay ecological sensitivity zones with development plans
- Create heatmaps of ecological crisis incidents

## Code Snippet Template
```python
import geopandas as gpd
from shapely.geometry import Point

# Create GeoDataFrame from ecological site data
gdf = gpd.GeoDataFrame(
    df,
    geometry=gpd.points_from_xy(df.longitude, df.latitude),
    crs='EPSG:4326'
)

# Spatial join with polygon boundaries
joined = gpd.sjoin(gdf, boundaries, how='inner', predicate='within')
```