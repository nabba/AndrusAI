# Geospatial Data Analysis

## Overview
This skill enables the team to process, analyze, and visualize geospatial ecological data using Python libraries.

## Key Techniques
1. Reading shapefiles with GeoPandas
2. Creating interactive maps with Folium
3. Spatial joins and overlays
4. Heatmap visualization

## Example Workflow
```python
import geopandas as gpd
import folium

data = gpd.read_file('habitat.shp')
map = folium.Map(location=[data.geometry.centroid.y.mean(), data.geometry.centroid.x.mean()])
folium.GeoJson(data).add_to(map)
map.save('habitat_map.html')
```