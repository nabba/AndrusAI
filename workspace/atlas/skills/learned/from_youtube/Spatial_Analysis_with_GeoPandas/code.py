import geopandas as gdf
# Load datasets
buildings = gdf.read_file('buildings.shp')
flood_zones = gdf.read_file('flood_zones.shp')
# Perform spatial join
risky_buildings = gdf.sjoin(buildings, flood_zones, how='inner', predicate='intersects')