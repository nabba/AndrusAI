import plotly.express as px
import pandas as pd

def visualize_data(data, chart_type='line', **kwargs):
    """
    Generate interactive visualizations from data
    Args:
        data: DataFrame or dict
        chart_type: line|bar|scatter|histogram
    Returns HTML string of visualization
    """
    df = pd.DataFrame(data)
    fig = getattr(px, chart_type)(df, **kwargs)
    return fig.to_html()