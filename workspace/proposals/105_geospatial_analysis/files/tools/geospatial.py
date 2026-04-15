import geopandas as gpd
import matplotlib.pyplot as plt

def plot_geodata(geojson_path, output_file='map.png'):
    """
    Create maps from GeoJSON data
    """
    gdf = gpd.read_file(geojson_path)
    gdf.plot()
    plt.savefig(output_file)
    return output_file

def spatial_join(data1, data2):
    """
    Perform spatial joins between datasets
    """
    gdf1 = gpd.GeoDataFrame(data1)
    gdf2 = gpd.GeoDataFrame(data2)
    return gpd.sjoin(gdf1, gdf2)