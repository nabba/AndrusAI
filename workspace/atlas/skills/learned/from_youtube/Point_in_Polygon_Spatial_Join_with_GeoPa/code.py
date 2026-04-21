import geopandas as gpd

# Load data
points = gpd.read_file('data/points.geojson')  # e.g., GPS locations
polygons = gpd.read_file('data/districts.geojson')  # e.g., administrative boundaries

# Ensure consistent CRS
if points.crs != polygons.crs:
    points = points.to_crs(polygons.crs)

# Perform spatial join (points within polygons)
result = gpd.sjoin(points, polygons, how='inner', predicate='within')

# Save or use result
result.to_file('data/joined_result.geojson', driver='GeoJSON')
print(f'Joined {len(result)} points to polygons.')