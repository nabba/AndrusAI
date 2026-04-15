# Geo-Spatial Analysis

## Overview
Learn to handle and analyze geo-spatial data for ecological insights.

## Key Concepts
- Geo-spatial data formats (e.g., Shapefiles, GeoJSON)
- Mapping ecological data
- Spatial analysis techniques

## Example
```python
import geopandas as gpd

# Load shapefile
data = gpd.read_file('ecological_data.shp')
# Plot data
data.plot()
```