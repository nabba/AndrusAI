import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd

def create_visualization(data, chart_type='line', **kwargs):
    """
    Creates ecological data visualizations
    Supported types: line, bar, scatter, heatmap, boxplot
    """
    df = pd.DataFrame(data)
    
    plt.figure(figsize=kwargs.get('figsize', (10,6)))
    
    if chart_type == 'line':
        sns.lineplot(data=df, **kwargs)
    elif chart_type == 'bar':
        sns.barplot(data=df, **kwargs)
    elif chart_type == 'scatter':
        sns.scatterplot(data=df, **kwargs)
    elif chart_type == 'heatmap':
        sns.heatmap(data=df, **kwargs)
    elif chart_type == 'boxplot':
        sns.boxplot(data=df, **kwargs)
    
    if title := kwargs.get('title'):
        plt.title(title)
    
    return plt.gcf()