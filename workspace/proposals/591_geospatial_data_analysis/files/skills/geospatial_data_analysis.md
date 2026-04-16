# Geospatial Data Analysis

## Overview
This skill covers the basics of geospatial data analysis for ecological applications, including mapping and spatial analysis.

## Tools
1. **GeoPandas**: For manipulating geospatial data.
2. **Folium**: For interactive mapping.
3. **Rasterio**: For raster data analysis.

## Techniques
1. **Mapping Ecological Data**: Creating maps to visualize species distribution or habitat changes.
2. **Spatial Analysis**: Calculating distances, buffers, and overlays for ecological studies.

## Example Code
```python
import geopandas as gpd
import folium

data = gpd.read_file('ecological_data.shp')
m = folium.Map(location=[45.5236, -122.6750], zoom_start=13)
folium.GeoJson(data).add_to(m)
m.save('map.html')
```