import geopandas as gpd
import matplotlib.pyplot as plt

def analyze_geospatial_data(shapefile_path, attribute=None):
    """
    Process and visualize geospatial ecological data
    """
    gdf = gpd.read_file(shapefile_path)
    
    # Basic analysis
    summary = {
        'crs': str(gdf.crs),
        'bounds': gdf.total_bounds.tolist(),
        'area': gdf.geometry.area.sum()
    }
    
    # Visualization
    fig, ax = plt.subplots(figsize=(10, 10))
    if attribute:
        gdf.plot(column=attribute, ax=ax, legend=True)
    else:
        gdf.plot(ax=ax)
    
    return {'summary': summary, 'plot': fig}