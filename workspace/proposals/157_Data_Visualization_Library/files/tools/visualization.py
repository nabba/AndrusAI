import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, Any

def create_ecological_chart(data: Dict, chart_type: str = 'line') -> Any:
    """
    Create ecological visualization charts
    Supported types: line, bar, scatter, heatmap
    """
    plt.style.use('seaborn')
    
    if chart_type == 'line':
        plt.plot(data['x'], data['y'])
    elif chart_type == 'bar':
        plt.bar(data['x'], data['y'])
    elif chart_type == 'scatter':
        plt.scatter(data['x'], data['y'])
    elif chart_type == 'heatmap':
        sns.heatmap(data['matrix'])

    plt.title(data.get('title', ''))
    plt.xlabel(data.get('xlabel', ''))
    plt.ylabel(data.get('ylabel', ''))
    return plt