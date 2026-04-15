import json
from d3 import render_chart

def visualize_data(data, chart_type):
    """
    Generate interactive visualizations from structured data
    Supported chart_types: line, bar, scatter, heatmap
    """
    return render_chart(json.dumps(data), chart_type)